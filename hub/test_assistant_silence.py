"""Assistant silent while the caller is talking, judged after the switch grace."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import call_tap
from call_tap import CallTap, judge_assistant_silence


def _pcm(seconds: float, rate: int, loud: bool, channels: int = 1) -> bytes:
    sample = b"\x00\x10" if loud else b"\x00\x00"
    return sample * channels * int(seconds * rate)


class JudgeMathTest(unittest.TestCase):
    def test_opening_blip_does_not_count_as_a_reply(self) -> None:
        # The failed call: one loud frame in the grace, then silence, while the caller talks.
        out = judge_assistant_silence(
            {"judge_seconds": 16.0, "judge_loud_frac": 0.12, "judge_rms": 0.04},
            {"judge_seconds": 16.0, "judge_loud_frac": 0.0, "judge_rms": 0.0},
            after_s=2.0,
            min_s=8.0,
        )
        self.assertTrue(out["assistant_silent"])
        self.assertEqual(out["spk_loud_frac"], 0.0)
        self.assertEqual(out["mic_loud_frac"], 0.12)

    def test_sustained_assistant_audio_is_not_silent(self) -> None:
        out = judge_assistant_silence(
            {"judge_seconds": 16.0, "judge_loud_frac": 0.2, "judge_rms": 0.05},
            {"judge_seconds": 16.0, "judge_loud_frac": 0.3, "judge_rms": 0.08},
            min_s=8.0,
        )
        self.assertFalse(out["assistant_silent"])

    def test_quiet_caller_is_not_flagged(self) -> None:
        out = judge_assistant_silence(
            {"judge_seconds": 16.0, "judge_loud_frac": 0.0, "judge_rms": 0.0},
            {"judge_seconds": 16.0, "judge_loud_frac": 0.0, "judge_rms": 0.0},
            min_s=8.0,
        )
        self.assertFalse(out["assistant_silent"])

    def test_short_window_is_not_flagged(self) -> None:
        out = judge_assistant_silence(
            {"judge_seconds": 3.0, "judge_loud_frac": 0.5, "judge_rms": 0.1},
            {"judge_seconds": 3.0, "judge_loud_frac": 0.0, "judge_rms": 0.0},
            min_s=8.0,
        )
        self.assertFalse(out["assistant_silent"])
        self.assertFalse(out["judged"])


class TapWindowTest(unittest.TestCase):
    def test_loudness_before_the_grace_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(call_tap, "REPLY_JUDGE_MIN_S", 0.4):
            tap = CallTap("t", {"mode": "grokbot"}, record_dir=Path(tmp))
            # Caller audio before the switch must not enter the judged window.
            tap.write_s16(call_tap.GROK_MIC_STEM, _pcm(0.5, 8000, True), 8000, 1)
            tap.begin_reply_judge(0.1)
            tap.write_s16(call_tap.GROK_MIC_STEM, _pcm(0.1, 8000, False), 8000, 1)
            tap.write_s16(call_tap.GROK_MIC_STEM, _pcm(0.5, 8000, True), 8000, 1)
            # Opening blip on the speaker stem, then silence.
            tap.write_s16(call_tap.GROK_SPK_STEM, _pcm(0.1, 8000, True, 2), 8000, 2)
            tap.write_s16(call_tap.GROK_SPK_STEM, _pcm(0.5, 8000, False, 2), 8000, 2)
            assessment = tap.assistant_silence()
            assert assessment is not None
            self.assertTrue(assessment["assistant_silent"])
            self.assertEqual(assessment["spk_loud_frac"], 0.0)
            self.assertGreaterEqual(assessment["mic_loud_frac"], 0.05)
            summary = tap.summary()
            self.assertTrue(summary["assistant_silent"])
            self.assertEqual(summary["assistant_silent_stats"]["spk_loud_frac"], 0.0)
            summary = asyncio.run(tap.close())
            levels = json.loads((tap.dir / "levels.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["assistant_silent"])
            self.assertTrue(levels["assistant_silent"])
            self.assertEqual(levels["assistant_silent_stats"]["spk_loud_frac"], 0.0)

    def test_stop_then_close_still_writes_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(call_tap, "REPLY_JUDGE_MIN_S", 0.2):
            tap = CallTap("t", {"mode": "grokbot"}, record_dir=Path(tmp))
            tap.begin_reply_judge(0.0)
            tap.write_s16(call_tap.GROK_MIC_STEM, _pcm(0.3, 8000, True), 8000, 1)
            tap.write_s16(call_tap.GROK_SPK_STEM, _pcm(0.3, 8000, False, 2), 8000, 2)
            tap.note_page_state("hangup", {"ui": {"muted": True}})

            async def _finish() -> None:
                await tap.stop_capture()
                await tap.close()

            asyncio.run(_finish())
            levels = json.loads((tap.dir / "levels.json").read_text(encoding="utf-8"))
            self.assertTrue(levels["assistant_silent"])
            self.assertTrue(levels["meta"]["grok_page_at_hangup"]["ui"]["muted"])

    def test_unarmed_tap_has_no_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tap = CallTap("t", {"mode": "grokbot"}, record_dir=Path(tmp))
            tap.write_s16(call_tap.GROK_MIC_STEM, _pcm(0.2, 8000, True), 8000, 1)
            self.assertIsNone(tap.assistant_silence())
            self.assertNotIn("assistant_silent", tap.summary())
            asyncio.run(tap.close())


class HealthCopyTest(unittest.TestCase):
    def test_live_assessment_wins_over_an_older_tap(self) -> None:
        live = {"assistant_silent": True, "assistant_silent_stats": {"mic_loud_frac": 0.2}}
        health = {
            "assistant_silent": live,
            "last_call_tap": {"assistant_silent": False, "assistant_silent_stats": {}},
        }
        self.assertIs(call_tap.assistant_silence_from_health(health), live)

    def test_levels_meta_supplies_the_page_readout(self) -> None:
        found = call_tap.assistant_silence_from_health(
            {
                "last_call_tap": {
                    "assistant_silent": True,
                    "assistant_silent_stats": {"spk_loud_frac": 0.0},
                    "meta": {
                        "grok_page_at_switch": {"ui": {"live": True}},
                        "grok_page_at_hangup": {"ui": {"live": False, "muted": True}},
                    },
                }
            }
        )
        assert found is not None
        self.assertTrue(found["assistant_silent"])
        self.assertEqual(found["grok_page"]["at_hangup"]["ui"]["muted"], True)


class PageStateTest(unittest.IsolatedAsyncioTestCase):
    def test_status_keeps_ui_and_drops_unknown_devices(self) -> None:
        from grokbot_voice import page_state_from_status

        state = page_state_from_status(
            {
                "json": {
                    "cdp_up": True,
                    "ui": {"live": True, "muted": False, "idle": False, "session": "voice"},
                    "devices": {"grok_mic": True, "grok_bus": True, "other": "nope"},
                }
            }
        )
        self.assertEqual(state["ui"]["session"], "voice")
        self.assertFalse(state["ui"]["muted"])
        self.assertEqual(state["devices"], {"grok_bus": True, "grok_mic": True})
        self.assertTrue(state["cdp_up"])

    async def test_read_page_state_records_a_failed_status(self) -> None:
        from grokbot_voice import GrokBotVoice

        voice = GrokBotVoice("suzie")
        with patch(
            "grokbot_voice.run_patchbay",
            return_value={"rc": 3, "meaning": "cdp down", "json": None},
        ):
            state = await voice.read_page_state()
        self.assertEqual(state, {"error": "cdp down"})


class CallEndedPayloadTest(unittest.TestCase):
    def test_call_ended_includes_the_flag_and_stats(self) -> None:
        import call_end_watch as cew

        health = {
            "ok": True,
            "call": {"busy": False, "e2e": False, "last_ws_s": None, "last_uplink_s": None, "path": "grokbot"},
            "talk": {},
            "assistant_silent": {
                "assistant_silent": True,
                "assistant_silent_stats": {
                    "judged": True,
                    "after_s": 2.0,
                    "mic_loud_frac": 0.11,
                    "spk_loud_frac": 0.0,
                    "mic_judge_s": 16.0,
                    "spk_judge_s": 15.5,
                    "mic_rms": 0.04,
                    "spk_rms": 0.0,
                },
                "grok_page": {"at_switch": {"ui": {"live": True}}, "at_hangup": {"ui": {"muted": True}}},
            },
        }
        st = cew.WatchState(call_id="phone-1", offhook_wall="2026-11-09T20:49:40+00:00")
        with patch.object(cew, "journal_summary", return_value={}), patch.object(cew, "hub_health", return_value=health):
            payload = cew.build_payload(st=st, duration_s=20.0, test=True, health=health, now_ts=1_000.0)
        self.assertTrue(payload["assistant_silent"])
        self.assertEqual(payload["assistant_silent_stats"]["spk_loud_frac"], 0.0)
        self.assertEqual(payload["assistant_silent_stats"]["mic_loud_frac"], 0.11)
        self.assertEqual(payload["grok_page"]["at_hangup"]["ui"]["muted"], True)
        self.assertIn("assistant_silent=true", payload["text"])

    def test_ordinary_call_omits_the_flag(self) -> None:
        import call_end_watch as cew

        health = {"ok": True, "call": {"busy": False, "e2e": False}, "talk": {}}
        st = cew.WatchState(call_id="phone-2")
        with patch.object(cew, "journal_summary", return_value={}):
            payload = cew.build_payload(st=st, duration_s=5.0, test=True, health=health, now_ts=1_000.0)
        self.assertNotIn("assistant_silent", payload)
        self.assertNotIn("assistant_silent=true", payload["text"])


class HubWatchLevelsTest(unittest.TestCase):
    def test_levels_json_is_copied_onto_the_fallback_alert(self) -> None:
        import hub_call_watch

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "20261109T204940Z-grokbot"
            folder.mkdir()
            (folder / "levels.json").write_text(
                json.dumps(
                    {
                        "assistant_silent": True,
                        "assistant_silent_stats": {"mic_loud_frac": 0.2, "spk_loud_frac": 0.0},
                        "meta": {"grok_page_at_switch": {"ui": {"live": True}}},
                    }
                ),
                encoding="utf-8",
            )
            posted: list[dict] = []
            with patch.object(hub_call_watch, "time") as clock, patch.object(
                hub_call_watch, "adb_fired_since", return_value=False
            ), patch.object(hub_call_watch, "post_alert_event", side_effect=lambda p: posted.append(p)), patch.object(
                hub_call_watch, "transcript_pointer", return_value=None
            ), patch(
                "grokbot_transcript.post_transcript_followup"
            ), patch.dict("os.environ", {"GSM2COMPUTER_CALL_RECORD_DIR": tmp}):
                clock.sleep.return_value = None
                clock.time.return_value = 200.0
                hub_call_watch.on_end(100.0, folder.name, "closed")
        self.assertEqual(len(posted), 1)
        self.assertTrue(posted[0]["assistant_silent"])
        self.assertEqual(posted[0]["assistant_silent_stats"]["spk_loud_frac"], 0.0)
        self.assertEqual(posted[0]["grok_page"]["at_switch"]["ui"]["live"], True)


if __name__ == "__main__":
    unittest.main()
