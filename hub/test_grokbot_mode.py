#!/usr/bin/env python3
"""AUTO_MODE=grokbot: patchbay lifecycle, health gating, call review, e2e helpers."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import grokbot_voice as gv  # noqa: E402
from call_slot import disruptive_heal_blocked  # noqa: E402

FAKE_PATCHBAY = """#!/usr/bin/env bash
# Fake patchbay: logs argv, exit code per subcommand from env/files.
echo "$@" >> "$FAKE_LOG"
sub="$1"
codefile="$FAKE_DIR/$sub.codes"
code=0
if [[ -f "$codefile" ]]; then
  code=$(head -n1 "$codefile"); tail -n +2 "$codefile" > "$codefile.tmp"; mv "$codefile.tmp" "$codefile"
  [[ -z "$code" ]] && code=0
fi
[[ -n "${FAKE_SLEEP:-}" && "$sub" == switch ]] && sleep "$FAKE_SLEEP"
if [[ "$code" == 0 ]]; then echo '{"ok":true,"bot":"Suzie"}'; else echo "fail $code" >&2; fi
exit "$code"
"""


class FakePatchbay:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="fakepb-"))
        self.bin = self.dir / "patchbay"
        self.bin.write_text(FAKE_PATCHBAY)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        self.log = self.dir / "log"
        os.environ["FAKE_LOG"] = str(self.log)
        os.environ["FAKE_DIR"] = str(self.dir)
        os.environ.pop("FAKE_SLEEP", None)

    def codes(self, sub: str, *codes: int) -> None:
        (self.dir / f"{sub}.codes").write_text("\n".join(str(c) for c in codes) + "\n")

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


def voice(pb: FakePatchbay) -> gv.GrokBotVoice:
    return gv.GrokBotVoice(bot="suzie", binary=str(pb.bin), switch_timeout_s=5, off_timeout_s=5, lock_retry_delay_s=0.01)


class GrokBotVoiceTest(unittest.TestCase):
    def test_switch_and_off(self) -> None:
        pb = FakePatchbay()
        v = voice(pb)
        res = asyncio.run(v.start())
        self.assertTrue(res["ok"])
        self.assertTrue(v.live)
        self.assertEqual(res["json"], {"ok": True, "bot": "Suzie"})
        asyncio.run(v.stop())
        self.assertFalse(v.live)
        self.assertEqual(pb.calls(), ["switch suzie --json", "off --json"])

    def test_lock_busy_retried_once(self) -> None:
        pb = FakePatchbay()
        pb.codes("switch", 4, 0)
        v = voice(pb)
        res = asyncio.run(v.start())
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("retried"))
        self.assertEqual(len(pb.calls()), 2)

    def test_lock_busy_twice_fails_no_third_try(self) -> None:
        pb = FakePatchbay()
        pb.codes("switch", 4, 4, 0)
        v = voice(pb)
        res = asyncio.run(v.start())
        self.assertFalse(res["ok"])
        self.assertEqual(res["rc"], 4)
        self.assertEqual(len(pb.calls()), 2)
        self.assertFalse(v.live)

    def test_routing_failure_not_live(self) -> None:
        pb = FakePatchbay()
        pb.codes("switch", 6)
        v = voice(pb)
        res = asyncio.run(v.start())
        self.assertFalse(res["ok"])
        self.assertEqual(res["meaning"], "audio routing failed")
        self.assertFalse(v.live)
        snap = v.snapshot()
        self.assertEqual(snap["last_switch"]["rc"], 6)

    def test_missing_binary(self) -> None:
        v = gv.GrokBotVoice(binary="/nonexistent/patchbay")
        res = asyncio.run(v.start())
        self.assertEqual(res["rc"], 127)
        self.assertFalse(res["ok"])

    def test_stop_later_waits_for_running_switch(self) -> None:
        pb = FakePatchbay()
        os.environ["FAKE_SLEEP"] = "0.3"
        v = voice(pb)

        async def scenario() -> None:
            sw = asyncio.create_task(v.start())
            await asyncio.sleep(0.05)
            t = v.stop_later(after=sw)
            await t
            # next call waits for the pending off before switching again
            await v.start()

        asyncio.run(scenario())
        self.assertEqual(pb.calls(), ["switch suzie --json", "off --json", "switch suzie --json"])

    def test_timeout(self) -> None:
        pb = FakePatchbay()
        os.environ["FAKE_SLEEP"] = "3"
        v = gv.GrokBotVoice(binary=str(pb.bin), switch_timeout_s=0.3)
        res = asyncio.run(v.start())
        self.assertEqual(res["rc"], 124)
        self.assertFalse(v.live)

    def test_gain(self) -> None:
        import audioop
        pcm = audioop.mul(b"\x00\x10" * 10, 2, 1.0)
        self.assertEqual(gv.apply_gain(pcm, 1.0), pcm)
        self.assertEqual(audioop.max(gv.apply_gain(b"\xff\x7f" * 4, 4.0), 2), 32767)


class HealthGateTest(unittest.TestCase):
    def test_grok_live_blocks_heal(self) -> None:
        idle = {"call": {"busy": False}, "talk": {"talk_active": False}}
        self.assertFalse(disruptive_heal_blocked(idle))
        self.assertTrue(disruptive_heal_blocked({**idle, "grokbot": {"live": True}}))
        self.assertTrue(disruptive_heal_blocked({**idle, "grokbot": {"switching": True}}))


class HubModeTest(unittest.TestCase):
    def _probe(self, mode: str) -> dict:
        code = (
            "import json,hub;"
            "print(json.dumps({'grok':hub.GROKBOT_ON_CALL,'auto':hub.AUTO_MODE_ON_CALL}))"
        )
        env = {**os.environ, "GSM2COMPUTER_AUTO_MODE": mode, "GSM2COMPUTER_PORTAL_DB": "/tmp/test-grokbot-portal.sqlite"}
        out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_mode_selection(self) -> None:
        self.assertTrue(self._probe("grokbot")["grok"])
        self.assertFalse(self._probe("openclaw")["grok"])

    def test_silent_grok_call_requests_live_e2e(self) -> None:
        import hub
        with tempfile.TemporaryDirectory() as td:
            req = Path(td) / "live-request"
            tap = {
                "id": "20261019T120000Z-grokbot",
                "meta": {"mode": "grokbot", "e2e": False},
                "streams": {
                    "gsm-downlink-8k-mono": {"seconds": 30, "peak": 0.25},  # hold tone only
                    "grok-mic-48k-stereo": {"seconds": 30, "peak": 0.3},
                },
            }
            with patch.object(hub, "E2E_LIVE_REQUEST", req), patch.object(
                hub.asyncio, "get_running_loop", side_effect=RuntimeError
            ):
                hub._maybe_schedule_e2e_after_silent_call(tap)
            self.assertTrue(req.is_file())
            req.unlink()
            tap["streams"]["grok-spk-48k-stereo"] = {"seconds": 28, "peak": 0.3}
            with patch.object(hub, "E2E_LIVE_REQUEST", req):
                hub._maybe_schedule_e2e_after_silent_call(tap)
            self.assertFalse(req.exists())

    def test_switchboard_has_grokbot(self) -> None:
        text = (HERE / "switchboard.sh").read_text()
        self.assertIn("grokbot)", text)

    def test_sink_capture_covers_grok_bus(self) -> None:
        from pipewire_target import record_uses_sink_capture
        self.assertTrue(record_uses_sink_capture("grok_bus"))
        self.assertTrue(record_uses_sink_capture("grok_bus.monitor"))
        self.assertFalse(record_uses_sink_capture("grok_mic"))


class CallReviewTest(unittest.TestCase):
    def test_journal_summary_uses_grok_taps(self) -> None:
        import call_end_watch as cew
        health = {"last_call_tap": {"id": "x-grokbot", "streams": {
            "grok-spk-48k-stereo": {"peak": 0.4},
            "grok-mic-48k-stereo": {"peak": 0.2},
            "gsm-downlink-8k-mono": {"peak": 0.3},
            "openclaw-spk-48k-stereo": {"peak": 0.0},
        }}}
        with patch.object(cew, "hub_health", return_value=health), patch.object(
            cew.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")
        ):
            out = cew.journal_summary()
        self.assertEqual(out["bot_path"], "grokbot")
        self.assertEqual(out["spk_peak"], 0.4)
        self.assertEqual(out["mic_peak"], 0.2)
        self.assertTrue(out["openclaw_replied_suspected"])
        # hold tone on downlink alone must not count as a bot reply
        health["last_call_tap"]["streams"]["grok-spk-48k-stereo"] = {"peak": 0.0}
        with patch.object(cew, "hub_health", return_value=health), patch.object(
            cew.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")
        ):
            out = cew.journal_summary()
        self.assertFalse(out["openclaw_replied_suspected"])


class E2EHelpersTest(unittest.TestCase):
    def test_wav_tail_loud(self) -> None:
        import math
        import struct
        try:
            import openclaw_e2e as e2e
        except ImportError as exc:  # websockets missing outside the venv
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "spk.wav"
            rate = 8000
            quiet = b"\x00\x00" * rate * 3
            loud = b"".join(struct.pack("<h", int(8000 * math.sin(i / 3))) for i in range(rate * 2))
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes(loud + quiet + loud)
            tail_loud, total = e2e.wav_tail_loud_s(p, 2.0)
            self.assertAlmostEqual(total, 7.0, places=1)
            self.assertGreaterEqual(tail_loud, 1.8)
            tail_loud, _ = e2e.wav_tail_loud_s(p, 4.5)
            self.assertLess(tail_loud, 3.0)


class GrokbotUplinkWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_grokbot_uplink_goes_through_side_writer_with_gain(self) -> None:
        import audioop
        import types
        import hub

        class Stdin:
            def __init__(self) -> None:
                self.data = b""
                self.transport = types.SimpleNamespace(get_write_buffer_size=lambda: 0)

            def write(self, data: bytes) -> None:
                self.data += data

            async def drain(self) -> None:
                return None

        stdin = Stdin()
        bridge = hub.PipewireBridge(gv.GROKBOT_MIC_SINK, gv.GROKBOT_SPK_SINK, 48000)
        bridge._playback = types.SimpleNamespace(stdin=stdin, returncode=None)  # type: ignore[assignment]
        bridge.uplink_gain = 2.0
        bridge._uplink_q = asyncio.Queue(maxsize=25)
        task = asyncio.create_task(bridge._pump_uplink())
        frame = b"\x00\x01" * 960  # 20 ms @ 48k
        bridge.offer_uplink(frame, "audio/pcm", 48000)
        await asyncio.sleep(0.1)
        self.assertEqual(bridge.uplink_written, 1)
        self.assertEqual(audioop.max(stdin.data, 2), 2 * audioop.max(frame, 2))
        snap = bridge.downlink_snapshot()
        self.assertEqual(snap["uplink_dropped_frames"], 0)
        bridge._closed = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    unittest.main()
