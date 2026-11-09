#!/usr/bin/env python3
"""Per-call taps between the phone, PipeWire buses, and OpenClaw.

Capture still writes WAV stems during the call. close() mixes each room into
one stereo MP3 (left = that room's uplink/mic, right = downlink/speaker) and
deletes the WAVs on success:

  gsm.mp3            phone room (16 kHz): uplink L, downlink R
  openclaw.mp3       OpenClaw/Chromium room (48 kHz): mic L, speaker R
  openclaw-relay.mp3 relay append L + tts R (24 kHz), if those stems exist

Internal stem names are unchanged:

  phone μ-law WS  →  gsm-uplink-8k-mono.wav
  hub 8 kHz capture before μ-law  →  gsm-downlink-8k-mono.wav
  phone_uplink.monitor (Chromium mic)  →  openclaw-mic-48k-stereo.wav
  openclaw_bus (Chromium speaker, sink capture)  →  openclaw-spk-48k-stereo.wav

relay graph also writes openclaw-append-24k-mono.wav and openclaw-tts-24k-mono.wav.

Every call, in every mode, also writes phone-downlink-8k-mono.wav: the mono PCM
handed to the phone, resampled to 8 kHz. That file is not part of a room mix,
so close() leaves it in place. Listen with any WAV player, or
``ffprobe <call-dir>/phone-downlink-8k-mono.wav``.

Grok Bot calls may also contain transcript.json. Read it with
``jq -r '.turns[] | [.speaker, (.timestamp // "-"), .text] | @tsv' <call-dir>/transcript.json``.

Directories are pruned to GSM2COMPUTER_CALL_RECORD_KEEP (default 20), oldest
name first, including the downlink and the transcript.

Disable with GSM2COMPUTER_CALL_RECORD=0.
"""
from __future__ import annotations

import audioop
import asyncio
import json
import logging
import os
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipewire_target import (
    pipewire_stream_env,
    pw_cat_raw_args,
    pw_latency_args,
    record_uses_sink_capture,
    resolve_pipewire_record_target,
    stdbuf_unbuffered,
)

LOG = logging.getLogger("gsm2computer-hub")

RECORD_ENABLED = os.environ.get("GSM2COMPUTER_CALL_RECORD", "1").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
RECORD_DIR = Path(
    os.environ.get(
        "GSM2COMPUTER_CALL_RECORD_DIR",
        str(Path.home() / "gsm2computer-calls"),
    )
)
KEEP_CALLS = int(os.environ.get("GSM2COMPUTER_CALL_RECORD_KEEP", "20"))
PHONE_DOWNLINK = "phone-downlink-8k-mono"
PHONE_DOWNLINK_RATE = 8000
# About two seconds of 20 ms frames. A stalled disk drops audio instead of stalling the pump.
_DOWNLINK_QUEUE_MAX = 100
# Shorter than the hub's 5s stop_capture cap. A stuck write returns so the call slot can be released.
DOWNLINK_FLUSH_S = float(os.environ.get("GSM2COMPUTER_DOWNLINK_FLUSH_S", "1"))
MIX_TIMEOUT_S = 30.0

GROK_MIC_STEM = "grok-mic-48k-stereo"
GROK_SPK_STEM = "grok-spk-48k-stereo"
# Ignore the hold-tone tail. The judged window starts this long after switch.
REPLY_JUDGE_AFTER_S = float(os.environ.get("GSM2COMPUTER_ASSISTANT_SILENT_AFTER_S", "2"))
# Both stems must have at least this much judged audio before the flag can be true.
REPLY_JUDGE_MIN_S = float(os.environ.get("GSM2COMPUTER_ASSISTANT_SILENT_MIN_S", "8"))
REPLY_MIC_LOUD_MIN = float(os.environ.get("GSM2COMPUTER_ASSISTANT_SILENT_MIC_LOUD", "0.05"))
REPLY_SPK_LOUD_MAX = float(os.environ.get("GSM2COMPUTER_ASSISTANT_SILENT_SPK_LOUD", "0.02"))

# out name, left stem, right stem, mix sample rate
_ROOM_MIXES: tuple[tuple[str, str, str, int], ...] = (
    ("gsm.mp3", "gsm-uplink-8k-mono", "gsm-downlink-8k-mono", 16000),
    ("openclaw.mp3", "openclaw-mic-48k-stereo", "openclaw-spk-48k-stereo", 48000),
    ("openclaw-relay.mp3", "openclaw-append-24k-mono", "openclaw-tts-24k-mono", 24000),
)


def _enabled() -> bool:
    return RECORD_ENABLED


def judge_assistant_silence(
    mic: dict[str, Any],
    spk: dict[str, Any],
    *,
    after_s: float = REPLY_JUDGE_AFTER_S,
    min_s: float = REPLY_JUDGE_MIN_S,
    mic_loud_min: float = REPLY_MIC_LOUD_MIN,
    spk_loud_max: float = REPLY_SPK_LOUD_MAX,
) -> dict[str, Any]:
    """True when the caller talked and Suzie did not, after the post-switch grace.

    ``judge_loud_frac`` is the fraction of 20 ms windows at or above rms 0.02,
    counted only once ``after_s`` has elapsed on that stem. A short opening
    blip inside the grace does not count as a reply.
    """
    mic_s = float(mic.get("judge_seconds") or 0.0)
    spk_s = float(spk.get("judge_seconds") or 0.0)
    mic_frac = float(mic.get("judge_loud_frac") or 0.0)
    spk_frac = float(spk.get("judge_loud_frac") or 0.0)
    enough = mic_s >= min_s and spk_s >= min_s
    silent = enough and mic_frac >= mic_loud_min and spk_frac <= spk_loud_max
    return {
        "assistant_silent": silent,
        "judged": enough,
        "after_s": after_s,
        "mic_loud_frac": mic_frac,
        "spk_loud_frac": spk_frac,
        "mic_judge_s": round(mic_s, 3),
        "spk_judge_s": round(spk_s, 3),
        "mic_rms": float(mic.get("judge_rms") or 0.0),
        "spk_rms": float(spk.get("judge_rms") or 0.0),
    }


def assistant_silence_from_health(health: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The assessment published on /health, else the one stored on the last tap."""
    live = health.get("assistant_silent")
    if isinstance(live, dict) and "assistant_silent" in live:
        return live
    tap = health.get("last_call_tap")
    if not isinstance(tap, dict) or "assistant_silent" not in tap:
        return None
    stats = tap.get("assistant_silent_stats")
    out: dict[str, Any] = {
        "assistant_silent": bool(tap.get("assistant_silent")),
        "assistant_silent_stats": stats if isinstance(stats, dict) else {},
    }
    meta = tap.get("meta") if isinstance(tap.get("meta"), dict) else {}
    pages: dict[str, Any] = {}
    if "grok_page_at_switch" in meta:
        pages["at_switch"] = meta["grok_page_at_switch"]
    if "grok_page_at_hangup" in meta:
        pages["at_hangup"] = meta["grok_page_at_hangup"]
    if pages:
        out["grok_page"] = pages
    return out


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        if rate <= 0:
            raise ValueError(f"invalid sample rate in {path.name}")
        return wav.getnframes() / rate


def ffmpeg_mix_args(left: Path, right: Path, out: Path, rate: int) -> list[str]:
    if rate <= 0:
        raise ValueError(f"invalid mix rate {rate}")
    whole_dur = max(_wav_seconds(left), _wav_seconds(right))
    if whole_dur <= 0:
        raise ValueError(f"empty stems: {left.name}, {right.name}")
    filt = (
        f"[0:a]aformat=sample_fmts=s16:channel_layouts=mono,aresample={rate}:async=1,apad=whole_dur={whole_dur:.6f}[l];"
        f"[1:a]aformat=sample_fmts=s16:channel_layouts=mono,aresample={rate}:async=1,apad=whole_dur={whole_dur:.6f}[r];"
        f"[l][r]join=inputs=2:channel_layout=stereo[a]"
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(left),
        "-i",
        str(right),
        "-filter_complex",
        filt,
        "-map",
        "[a]",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(out),
    ]


async def mix_stereo_mp3(
    left: Path,
    right: Path,
    out: Path,
    rate: int,
    timeout: float = MIX_TIMEOUT_S,
) -> dict[str, Any]:
    """Mix left/right WAVs into a stereo MP3. Raises on failure."""
    if not left.is_file():
        raise FileNotFoundError(left)
    if not right.is_file():
        raise FileNotFoundError(right)
    args = ffmpeg_mix_args(left, right, out, rate)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("ffmpeg not found") from exc
    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        _unlink_quiet(out)
        raise TimeoutError(f"ffmpeg mix timed out after {timeout}s: {out.name}")
    if proc.returncode != 0:
        _unlink_quiet(out)
        err = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise RuntimeError(f"ffmpeg mix {out.name} exited {proc.returncode}: {err}")
    if not out.is_file() or out.stat().st_size == 0:
        _unlink_quiet(out)
        raise RuntimeError(f"ffmpeg produced no output {out.name}")
    return {
        "file": out.name,
        "rate": rate,
        "channels": 2,
        "bytes": out.stat().st_size,
    }


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def _ffmpeg_ok() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return False
    try:
        await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return False
    return proc.returncode == 0


class _Stream:
    def __init__(self, path: Path, rate: int, channels: int) -> None:
        self.path = path
        self.rate = rate
        self.channels = channels
        self.wav = wave.open(str(path), "wb")
        self.wav.setnchannels(channels)
        self.wav.setsampwidth(2)
        self.wav.setframerate(rate)
        self.nbytes = 0
        self.peak = 0
        self.sum_sq = 0.0
        self.loud_windows = 0
        self.windows = 0
        self._judge_skip: Optional[int] = None
        self.judge_nbytes = 0
        self.judge_loud_windows = 0
        self.judge_windows = 0
        self.judge_sum_sq = 0.0

    def judge_after(self, seconds: float) -> None:
        """Count loudness only once `seconds` more audio has been written."""
        if self._judge_skip is not None:
            return
        frame = 2 * self.channels
        skip = max(0.0, float(seconds))
        self._judge_skip = self.nbytes + int(skip * self.rate) * frame

    def write(self, pcm: bytes) -> None:
        if not pcm:
            return
        extra = len(pcm) % (2 * self.channels)
        if extra:
            pcm = pcm[: len(pcm) - extra]
        if not pcm:
            return
        start = self.nbytes
        self.wav.writeframes(pcm)
        self.nbytes += len(pcm)
        width = 2
        rms = audioop.rms(pcm, width)
        if rms > self.peak:
            self.peak = rms
        # One window per write is good enough for 20 ms frames.
        self.windows += 1
        self.sum_sq += float(rms) * float(rms)
        loud = rms / 32768.0 > 0.02
        if loud:
            self.loud_windows += 1
        if self._judge_skip is not None and start >= self._judge_skip:
            self.judge_nbytes += len(pcm)
            self.judge_windows += 1
            self.judge_sum_sq += float(rms) * float(rms)
            if loud:
                self.judge_loud_windows += 1

    def stats(self) -> dict[str, Any]:
        samples = self.nbytes // (2 * self.channels)
        seconds = samples / self.rate if self.rate else 0.0
        mean_rms = (self.sum_sq / self.windows) ** 0.5 / 32768.0 if self.windows else 0.0
        out = {
            "file": self.path.name,
            "rate": self.rate,
            "channels": self.channels,
            "seconds": round(seconds, 3),
            "peak": round(self.peak / 32768.0, 4),
            "rms": round(mean_rms, 4),
            "loud_frac": round(self.loud_windows / self.windows, 3) if self.windows else 0.0,
        }
        if self._judge_skip is not None:
            judge_samples = self.judge_nbytes // (2 * self.channels)
            judge_seconds = judge_samples / self.rate if self.rate else 0.0
            judge_rms = (
                (self.judge_sum_sq / self.judge_windows) ** 0.5 / 32768.0 if self.judge_windows else 0.0
            )
            out["judge_seconds"] = round(judge_seconds, 3)
            out["judge_rms"] = round(judge_rms, 4)
            out["judge_loud_frac"] = (
                round(self.judge_loud_windows / self.judge_windows, 3) if self.judge_windows else 0.0
            )
        return out

    def close(self) -> None:
        self.wav.close()


class CallTap:
    def __init__(self, call_id: str, meta: dict[str, Any], record_dir: Optional[Path] = None) -> None:
        self.call_id = call_id
        root = record_dir or RECORD_DIR
        self.dir = root / call_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = meta
        self._streams: dict[str, _Stream] = {}
        self._procs: list[asyncio.subprocess.Process] = []
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        self._downlink_ratecv: Any = None
        self._downlink_src_rate: Optional[int] = None
        self._down_q: list[bytes] = []
        self._down_scheduled = False
        self._down_task: Optional[asyncio.Task] = None
        self._down_drop_logged = False
        self._down_stop = False
        self._reply_judged = False
        self._reply_judge_after_s = REPLY_JUDGE_AFTER_S
        self._judge_from_start: dict[str, float] = {}
        self.started_wall = datetime.now(timezone.utc).timestamp()
        LOG.info("call tap dir %s", self.dir)

    @classmethod
    def maybe_open(
        cls,
        *,
        loopback: bool,
        mode: str,
        e2e: bool = False,
    ) -> Optional["CallTap"]:
        if not _enabled():
            return None
        record_dir = RECORD_DIR
        if e2e:
            record_dir = Path(
                os.environ.get(
                    "GSM2COMPUTER_E2E_RECORD_DIR",
                    str(Path.home() / "gsm2computer-e2e-calls"),
                )
            )
        record_dir.mkdir(parents=True, exist_ok=True)
        _prune(record_dir, KEEP_CALLS)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if e2e:
            kind = "e2e-test"
        elif loopback:
            kind = "loopback"
        else:
            kind = mode
        call_id = f"{stamp}-{kind}"
        return cls(
            call_id,
            {"loopback": loopback, "mode": mode, "id": call_id, "e2e": e2e},
            record_dir=record_dir,
        )

    def write_s16(self, name: str, pcm: bytes, rate: int, channels: int = 1) -> None:
        if self._closed or not pcm:
            return
        stream = self._streams.get(name)
        if stream is None:
            path = self.dir / f"{name}.wav"
            stream = _Stream(path, rate, channels)
            self._streams[name] = stream
            if name in self._judge_from_start:
                stream.judge_after(self._judge_from_start[name])
        stream.write(pcm)

    def begin_reply_judge(self, after_s: float = REPLY_JUDGE_AFTER_S) -> None:
        """Start the post-switch window. Mic is already recording; spk may not be."""
        self._reply_judged = True
        self._reply_judge_after_s = after_s
        self._judge_from_start[GROK_SPK_STEM] = after_s
        mic = self._streams.get(GROK_MIC_STEM)
        if mic is not None:
            mic.judge_after(after_s)
        else:
            self._judge_from_start[GROK_MIC_STEM] = after_s
        spk = self._streams.get(GROK_SPK_STEM)
        if spk is not None:
            spk.judge_after(after_s)

    def note_page_state(self, when: str, state: dict[str, Any]) -> None:
        if when not in {"switch", "hangup"}:
            return
        self.meta[f"grok_page_at_{when}"] = state

    def assistant_silence(self) -> Optional[dict[str, Any]]:
        if not self._reply_judged:
            return None
        mic = self._streams.get(GROK_MIC_STEM)
        spk = self._streams.get(GROK_SPK_STEM)
        if mic is None or spk is None:
            return None
        return judge_assistant_silence(
            mic.stats(),
            spk.stats(),
            after_s=self._reply_judge_after_s,
            min_s=REPLY_JUDGE_MIN_S,
            mic_loud_min=REPLY_MIC_LOUD_MIN,
            spk_loud_max=REPLY_SPK_LOUD_MAX,
        )

    def write_phone_downlink(self, pcm: bytes, rate: int) -> None:
        """Record mono s16 that the hub is handing to the phone, at 8 kHz.

        Used for every mode, including loopback. Existing stems are unchanged.
        """
        if self._closed or not pcm or rate <= 0:
            return
        extra = len(pcm) % 2
        if extra:
            pcm = pcm[:-extra]
        if not pcm:
            return
        if rate != PHONE_DOWNLINK_RATE:
            if self._downlink_src_rate != rate:
                self._downlink_ratecv = None
                self._downlink_src_rate = rate
            pcm, self._downlink_ratecv = audioop.ratecv(
                pcm, 2, 1, rate, PHONE_DOWNLINK_RATE, self._downlink_ratecv
            )
        else:
            self._downlink_ratecv = None
            self._downlink_src_rate = rate
        if not pcm:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None or self._down_stop:
            self.write_s16(PHONE_DOWNLINK, pcm, PHONE_DOWNLINK_RATE, 1)
            return
        if len(self._down_q) >= _DOWNLINK_QUEUE_MAX:
            self._down_q.pop(0)
            if not self._down_drop_logged:
                self._down_drop_logged = True
                LOG.warning("phone downlink record fell behind; dropping frames")
        self._down_q.append(pcm)
        if not self._down_scheduled:
            self._down_scheduled = True
            self._down_task = loop.create_task(self._drain_phone_downlink())

    async def _drain_phone_downlink(self) -> None:
        try:
            while self._down_q:
                chunk = self._down_q.pop(0)
                await asyncio.to_thread(self.write_s16, PHONE_DOWNLINK, chunk, PHONE_DOWNLINK_RATE, 1)
        finally:
            self._down_scheduled = False
            if self._down_q and not self._closed and not self._down_stop:
                self._down_scheduled = True
                self._down_task = asyncio.create_task(self._drain_phone_downlink())

    async def _join_downlink_writer(self, task: "asyncio.Task[None]", timeout_s: float) -> bool:
        """Wait up to timeout_s. Cancellation stays in place; this never calls uncancel."""
        if task.done() or timeout_s <= 0:
            return task.done()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except asyncio.TimeoutError:
            return False
        return True

    async def flush_phone_downlink(self) -> None:
        """Join the downlink writer for a bounded time, then the caller may close the WAV.

        A stuck ``write_s16`` returns so ``stop_capture`` can finish and the hub
        can release the call slot. An outer ``wait_for`` cancellation is not cleared.
        """
        self._down_stop = True
        deadline = time.monotonic() + DOWNLINK_FLUSH_S
        while True:
            task = self._down_task
            if task is not None and not task.done():
                remaining = deadline - time.monotonic()
                if not await self._join_downlink_writer(task, remaining):
                    LOG.warning(
                        "phone downlink writer still running after %.1fs; leaving it",
                        DOWNLINK_FLUSH_S,
                    )
                    return
                continue
            if not self._down_q:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOG.warning(
                    "phone downlink writer still running after %.1fs; leaving it",
                    DOWNLINK_FLUSH_S,
                )
                return
            chunk = self._down_q.pop(0)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.write_s16, PHONE_DOWNLINK, chunk, PHONE_DOWNLINK_RATE, 1),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                LOG.warning(
                    "phone downlink write still running after %.1fs; leaving it",
                    DOWNLINK_FLUSH_S,
                )
                return

    def write_ulaw(self, name: str, ulaw: bytes, rate: int = 8000) -> None:
        if self._closed or not ulaw:
            return
        self.write_s16(name, audioop.ulaw2lin(ulaw, 2), rate, 1)

    async def start_source(self, name: str, source: str, rate: int, channels: int) -> None:
        """pw-record a PipeWire monitor into a WAV (OpenClaw's ears/mouth)."""
        if self._closed:
            return
        try:
            capture_sink = record_uses_sink_capture(source)
            serial = await resolve_pipewire_record_target(source)
            raw = await pw_cat_raw_args()
            latency = pw_latency_args()
            env = pipewire_stream_env(rate, capture_sink=capture_sink)
            proc = await asyncio.create_subprocess_exec(
                *stdbuf_unbuffered(),
                "pw-record",
                *raw,
                *latency,
                "--media-category",
                "Capture",
                "--target",
                serial,
                "--rate",
                str(rate),
                "--channels",
                str(channels),
                "--format",
                "s16",
                "-",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as exc:
            LOG.error("call tap %s failed to start on %s: %s", name, source, exc)
            return
        self._procs.append(proc)
        self._tasks.append(asyncio.create_task(self._pump(proc, name, rate, channels, source)))
        LOG.info("call tap %s recording %s serial=%s capture_sink=%s %s/%sch", name, source, serial, capture_sink, rate, channels)

    async def _pump(
        self,
        proc: asyncio.subprocess.Process,
        name: str,
        rate: int,
        channels: int,
        source: str,
    ) -> None:
        assert proc.stdout
        read_size = max(channels * 2, (rate * channels * 2) // 50)
        try:
            while not self._closed:
                try:
                    chunk = await proc.stdout.readexactly(read_size)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        self.write_s16(name, exc.partial, rate, channels)
                    return
                self.write_s16(name, chunk, rate, channels)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                LOG.warning("call tap %s (%s) ended: %s", name, source, exc)
        finally:
            if proc.stderr:
                try:
                    err = await asyncio.wait_for(proc.stderr.read(), timeout=0.2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    err = b""
                if err and not self._closed:
                    LOG.warning(
                        "call tap %s stderr: %s",
                        name,
                        err.decode("utf-8", errors="replace")[:300],
                    )

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.call_id,
            "dir": str(self.dir),
            "streams": {name: stream.stats() for name, stream in self._streams.items()},
        }
        assessment = self.assistant_silence()
        if assessment is not None:
            out["assistant_silent"] = assessment["assistant_silent"]
            out["assistant_silent_stats"] = {
                key: assessment[key]
                for key in (
                    "judged",
                    "after_s",
                    "mic_loud_frac",
                    "spk_loud_frac",
                    "mic_judge_s",
                    "spk_judge_s",
                    "mic_rms",
                    "spk_rms",
                )
            }
        return out

    def _summary_with_transcript(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Keep a transcript already saved, including one written into levels.json."""
        try:
            from grokbot_transcript import load_saved_pointer

            pointer = load_saved_pointer(self.dir)
        except Exception:
            pointer = None
        if pointer:
            summary["transcript"] = pointer
            return summary
        levels = self.dir / "levels.json"
        if not levels.is_file():
            return summary
        try:
            existing = json.loads(levels.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return summary
        if isinstance(existing, dict) and isinstance(existing.get("transcript"), dict):
            summary["transcript"] = existing["transcript"]
        return summary

    def write_levels(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Rewrite levels.json without dropping a transcript or an earlier mix."""
        summary = self._summary_with_transcript(dict(summary))
        path = self.dir / "levels.json"
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(summary)
                summary = merged
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return summary

    async def close(self) -> dict[str, Any]:
        if self._closed and not self._procs and not self._tasks:
            # stop_capture already ran (the hub does that before close). Still
            # persist levels so the silence flag and page readout reach disk.
            summary = self._summary_with_transcript(self.summary())
            summary["meta"] = self.meta
            return self.write_levels(summary)
        await self.stop_capture()
        mixes: dict[str, Any] = {}
        try:
            mixes = await self._mix_rooms()
        except Exception as exc:
            LOG.error("call tap mix failed: %s; leaving WAV stems", exc)
        summary = self.summary()
        summary["meta"] = self.meta
        summary["mixes"] = mixes
        # Load the pointer immediately before the write so a transcript that
        # landed during the mix is not overwritten away.
        summary = self.write_levels(summary)
        LOG.info("call tap closed %s mixes=%s", json.dumps(summary["streams"]), json.dumps(mixes))
        return summary

    async def stop_capture(self) -> None:
        """Stop pw-record helpers and close WAV stems. Does not mix."""
        if self._closed and not self._procs and not self._tasks:
            return
        self._down_stop = True
        await self.flush_phone_downlink()
        writer_busy = self._down_task is not None and not self._down_task.done()
        self._closed = True
        for proc in self._procs:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
        for proc in self._procs:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
        for task in self._tasks:
            if not task.done():
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        self._tasks = []
        self._procs = []
        for name, stream in self._streams.items():
            if writer_busy and name == PHONE_DOWNLINK:
                continue
            try:
                stream.close()
            except Exception:
                pass

    async def _mix_rooms(self) -> dict[str, Any]:
        mixes: dict[str, Any] = {}
        if not await _ffmpeg_ok():
            LOG.error(
                "call tap mix: ffmpeg not found (`ffmpeg -version` failed); leaving WAV stems in %s",
                self.dir,
            )
            return mixes
        for out_name, left_name, right_name, rate in _ROOM_MIXES:
            left = self.dir / f"{left_name}.wav"
            right = self.dir / f"{right_name}.wav"
            if not left.is_file() or not right.is_file():
                continue
            out = self.dir / out_name
            left_stats = self._streams[left_name].stats() if left_name in self._streams else {}
            right_stats = self._streams[right_name].stats() if right_name in self._streams else {}
            stem_seconds = max(left_stats.get("seconds", 0.0), right_stats.get("seconds", 0.0))
            mix_timeout = max(MIX_TIMEOUT_S, stem_seconds * 0.05 + 60.0)
            try:
                stats = await mix_stereo_mp3(left, right, out, rate, timeout=mix_timeout)
            except Exception as exc:
                LOG.error("call tap mix %s failed: %s; leaving WAV stems", out_name, exc)
                continue
            stats["seconds"] = stem_seconds
            stats["left"] = left_name
            stats["right"] = right_name
            left.unlink()
            right.unlink()
            mixes[out_name] = stats
            LOG.info("call tap mixed %s", out_name)
        return mixes


def _prune(root: Path, keep: int) -> None:
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.name, reverse=True)
    for old in dirs[keep:]:
        for child in old.iterdir():
            child.unlink()
        old.rmdir()
        LOG.info("call tap pruned %s", old)
