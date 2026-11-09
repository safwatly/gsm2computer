"""Grok Bot live voice for GSM calls (AUTO_MODE=grokbot).

The hub owns the audio path; patchbay/grokcdp own the Grok Bot app:

  caller (WS uplink) -> hub pw-cat -> patchbay_mic sink -> grok_mic -> Grok Bot app
  Grok Bot app -> grok_bus sink -> hub pw-record (sink capture) -> WS downlink -> caller

Mix-minus is by construction: the hub only writes caller audio into
patchbay_mic and only reads grok_bus, so the bot never hears itself.
Call start runs ``patchbay switch <bot> --json``; call end runs
``patchbay off --json``. Exit 4 (mutation lock busy) is retried once.
Nothing here touches pactl or moves streams by hand.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger("gsm2computer-hub.grokbot")

PATCHBAY_BIN = os.environ.get("GSM2COMPUTER_PATCHBAY_BIN", str(Path.home() / "bin" / "patchbay"))
GROKBOT_BOT = os.environ.get("GSM2COMPUTER_GROKBOT_BOT", "suzie")
GROKBOT_MIC_SINK = os.environ.get("GSM2COMPUTER_GROKBOT_MIC_SINK", "patchbay_mic")
GROKBOT_MIC_SOURCE = os.environ.get("GSM2COMPUTER_GROKBOT_MIC_SOURCE", "grok_mic")
GROKBOT_SPK_SINK = os.environ.get("GSM2COMPUTER_GROKBOT_SPK_SINK", "grok_bus")
SWITCH_TIMEOUT_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_SWITCH_TIMEOUT_S", "60"))
OFF_TIMEOUT_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_OFF_TIMEOUT_S", "20"))
LOCK_RETRY_DELAY_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_LOCK_RETRY_S", "2"))
# How long a new call waits for the post-off transcript reader to abort.
# The reader is cancelled; the next switch does not wait out the CDP read.
TRANSCRIPT_ABORT_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_TRANSCRIPT_ABORT_S", "1"))
# Linear gain on caller audio before it reaches the bot (1.0 = unity).
UPLINK_GAIN = float(os.environ.get("GSM2COMPUTER_GROKBOT_UPLINK_GAIN", "1.0"))

_PAGE_DEVICE_NAMES = ("grok_bus", "grok_mic")


def page_state_from_status(status: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Small readout of ``patchbay status --json``. No DOM scrape and no click.

    Patchbay owns the Grok page. Whatever it puts under ``ui`` (live, muted,
    idle, session) is kept, capped so a large blob cannot land in call meta.
    """
    blob = status or {}
    nested = blob.get("json")
    if isinstance(nested, dict):
        blob = nested
    if not isinstance(blob, dict):
        return {}
    out: dict[str, Any] = {}
    ui = blob.get("ui")
    if isinstance(ui, dict) and ui:
        out["ui"] = {str(key): ui[key] for key in list(ui)[:12]}
    for key in ("cdp_up", "routing_rule_present"):
        if key in blob:
            out[key] = blob[key]
    devices = blob.get("devices")
    if isinstance(devices, dict):
        out["devices"] = {
            name: bool(devices.get(name)) for name in _PAGE_DEVICE_NAMES if name in devices
        }
    return out


EXIT_MEANING = {
    0: "ok",
    1: "runtime error",
    2: "bad args / unknown bot",
    3: "cdp down",
    4: "lock busy",
    5: "live did not start",
    6: "audio routing failed",
    7: "health unsafe",
}


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        # Some commands print several lines; take the last JSON object.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    val = json.loads(line)
                    return val if isinstance(val, dict) else None
                except json.JSONDecodeError:
                    continue
    return None


async def run_patchbay(*args: str, timeout_s: float, binary: Optional[str] = None) -> dict[str, Any]:
    """Run patchbay; return {rc, meaning, json, ms, error}. Never raises."""
    t0 = time.monotonic()
    cmd = [binary or PATCHBAY_BIN, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # so a timeout can kill grokcdp children too
        )
    except (FileNotFoundError, PermissionError) as exc:
        return {"rc": 127, "meaning": "missing binary", "json": None, "ms": 0, "error": str(exc)}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.CancelledError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        raise
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        return {
            "rc": 124,
            "meaning": "timeout",
            "json": None,
            "ms": int((time.monotonic() - t0) * 1000),
            "error": f"patchbay {' '.join(args)} timed out after {timeout_s:.1f}s",
        }
    rc = proc.returncode if proc.returncode is not None else 1
    out = out_b.decode("utf-8", "replace")
    err = err_b.decode("utf-8", "replace").strip()
    return {
        "rc": rc,
        "meaning": EXIT_MEANING.get(rc, "unknown"),
        "json": _parse_json(out),
        "ms": int((time.monotonic() - t0) * 1000),
        "error": err[-300:] if (rc and err) else None,
    }


class GrokBotVoice:
    """One Grok Bot live-voice session per GSM call, driven through patchbay."""

    def __init__(
        self,
        bot: str = GROKBOT_BOT,
        binary: Optional[str] = None,
        switch_timeout_s: float = SWITCH_TIMEOUT_S,
        off_timeout_s: float = OFF_TIMEOUT_S,
        lock_retry_delay_s: float = LOCK_RETRY_DELAY_S,
    ) -> None:
        self.bot = bot
        self.binary = binary
        self.switch_timeout_s = switch_timeout_s
        self.off_timeout_s = off_timeout_s
        self.lock_retry_delay_s = lock_retry_delay_s
        self.live = False
        self.switching = False
        self.last_switch: Optional[dict[str, Any]] = None
        self.last_off: Optional[dict[str, Any]] = None
        self._lock = asyncio.Lock()
        self._pending_off: Optional[asyncio.Task] = None
        self._off_done: Optional[asyncio.Event] = None
        self.transcript_abort: Optional[asyncio.Event] = None
        self.transcript_abort_s = TRANSCRIPT_ABORT_S
        self.on_transcript_abort: Optional[Any] = None

    async def _run_with_lock_retry(self, *args: str, timeout_s: float) -> dict[str, Any]:
        res = await run_patchbay(*args, timeout_s=timeout_s, binary=self.binary)
        if res["rc"] == 4:
            LOG.warning("patchbay %s: lock busy; retrying once", " ".join(args))
            await asyncio.sleep(self.lock_retry_delay_s)
            res = await run_patchbay(*args, timeout_s=timeout_s, binary=self.binary)
            res["retried"] = True
        return res

    async def wait_pending_off(self, timeout_s: float = 25.0) -> None:
        """Wait until ``patchbay off`` has returned. Does not wait for the transcript read."""
        done = self._off_done
        task = self._pending_off
        if task is None or task.done() or done is None or done.is_set():
            return
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except Exception:
            LOG.warning("pending grokbot off did not finish in %.0fs", timeout_s)

    async def _abort_transcript_reader(self) -> None:
        """Stop a post-off transcript read so the next switch cannot overlap a click."""
        abort = self.transcript_abort
        if abort is not None:
            abort.set()
        done = self._off_done
        task = self._pending_off
        # Off still owns patchbay. Leave that task alone; the hook sees the abort flag.
        if done is None or not done.is_set() or task is None or task.done():
            return
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.transcript_abort_s)
        except Exception:
            LOG.warning(
                "transcript reader still running after %.1fs; new call proceeds",
                self.transcript_abort_s,
            )

    async def start(self) -> dict[str, Any]:
        # Off must finish (it owns patchbay). The transcript read is aborted
        # and does not add more than transcript_abort_s before this switch.
        await self.wait_pending_off(timeout_s=self.off_timeout_s + self.lock_retry_delay_s + 5)
        await self._abort_transcript_reader()
        async with self._lock:
            self.switching = True
            try:
                res = await self._run_with_lock_retry(
                    "switch", self.bot, "--json", timeout_s=self.switch_timeout_s
                )
            finally:
                self.switching = False
            res["at"] = time.time()
            res["ok"] = res["rc"] == 0
            self.live = res["ok"]
            self.last_switch = res
            if res["ok"]:
                LOG.info("grokbot live: patchbay switch %s ok in %sms", self.bot, res["ms"])
            else:
                LOG.error(
                    "grokbot switch %s FAILED rc=%s (%s) in %sms err=%s",
                    self.bot, res["rc"], res["meaning"], res["ms"], res.get("error"),
                )
            return res

    async def read_page_state(self) -> dict[str, Any]:
        """One ``patchbay status --json``. Failures become ``{"error": ...}``."""
        res = await run_patchbay("status", "--json", timeout_s=3, binary=self.binary)
        state = page_state_from_status(res.get("json"))
        if not state and res.get("rc") not in (0, None):
            return {"error": res.get("meaning") or "status failed"}
        return state

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            res = await self._run_with_lock_retry("off", "--json", timeout_s=self.off_timeout_s)
            res["at"] = time.time()
            res["ok"] = res["rc"] == 0
            self.last_off = res
            if res["ok"]:
                self.live = False
                LOG.info("grokbot off ok in %sms", res["ms"])
            else:
                LOG.error("grokbot off FAILED rc=%s (%s) err=%s", res["rc"], res["meaning"], res.get("error"))
            return res

    def stop_later(self, after: Optional[asyncio.Task] = None, on_stopped: Optional[Any] = None) -> asyncio.Task:
        """End live voice in the background (after a still-running switch).

        ``on_stopped`` runs only after ``off`` returns, so a transcript read
        cannot click the Grok page while the voice session is still live.
        A new ``start`` aborts that read instead of waiting it out.
        """
        abort = asyncio.Event()
        off_done = asyncio.Event()
        self.transcript_abort = abort
        self._off_done = off_done

        async def _go() -> None:
            if after is not None and not after.done():
                try:
                    await asyncio.wait_for(asyncio.shield(after), timeout=self.switch_timeout_s + 5)
                except Exception:
                    after.cancel()
            res = await self.stop()
            off_done.set()
            if on_stopped is None:
                return
            try:
                await on_stopped(res)
            except asyncio.CancelledError:
                LOG.info("grok transcript aborted for a new call")
                callback = self.on_transcript_abort
                if callback is not None:
                    try:
                        callback()
                    except Exception:
                        LOG.warning("grok transcript abort callback failed", exc_info=True)
                return
            except Exception:
                LOG.warning("grokbot post-off hook failed", exc_info=True)

        task = asyncio.create_task(_go())
        self._pending_off = task
        return task

    def snapshot(self) -> dict[str, Any]:
        def _brief(r: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
            if not r:
                return None
            return {k: r.get(k) for k in ("ok", "rc", "meaning", "ms", "at", "retried") if k in r}

        return {
            "bot": self.bot,
            "live": self.live,
            "switching": self.switching,
            "mic_sink": GROKBOT_MIC_SINK,
            "spk_sink": GROKBOT_SPK_SINK,
            "last_switch": _brief(self.last_switch),
            "last_off": _brief(self.last_off),
        }


def apply_gain(pcm: bytes, gain: float = UPLINK_GAIN) -> bytes:
    """s16le gain with hard clip (audioop.mul saturates). gain<=1.0 is a no-op."""
    if gain == 1.0 or not pcm:
        return pcm
    import audioop  # same module hub.py uses (audioop-lts on 3.13)

    return audioop.mul(pcm, 2, gain)


_voice: Optional[GrokBotVoice] = None


def get_grokbot_voice() -> GrokBotVoice:
    global _voice
    if _voice is None:
        _voice = GrokBotVoice()
    return _voice
