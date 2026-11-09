#!/usr/bin/env python3
"""Debug downlink test tone: off by default, two pulses before bus audio."""
from __future__ import annotations

import array
import asyncio
import audioop
import base64
import json
import os
import unittest
from unittest.mock import patch

import hub


def _ws_text(frame: bytes) -> str:
    assert frame[0] == 0x81
    if frame[1] < 126:
        n = frame[1]
        return frame[2 : 2 + n].decode()
    n = int.from_bytes(frame[2:4], "big")
    return frame[4 : 4 + n].decode()


def _kind(pcm: bytes) -> str:
    rms = audioop.rms(pcm, 2) if pcm else 0
    if rms < 50:
        return "off"
    if rms > 5000:
        return "on"
    return "edge"


class _Stdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    async def readexactly(self, n: int) -> bytes:
        if not self.chunks:
            raise asyncio.IncompleteReadError(b"", n)
        chunk = self.chunks.pop(0)
        if len(chunk) != n:
            raise AssertionError((len(chunk), n))
        return chunk


class _Writer:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    def is_closing(self) -> bool:
        return False

    def get_extra_info(self, key: str) -> None:
        return None

    @property
    def transport(self) -> object:
        return type("T", (), {"get_write_buffer_size": staticmethod(lambda: 0)})()


class _Tap:
    def __init__(self) -> None:
        self.call_id = "phone-tone"
        self.meta: dict[str, object] = {}
        self.phone: list[tuple[bytes, int]] = []
        self.downlink: list[tuple[str, bytes, int]] = []

    def write_s16(self, name: str, pcm: bytes, rate: int, channels: int = 1) -> None:
        self.downlink.append((name, pcm, rate))

    def write_phone_downlink(self, pcm: bytes, rate: int) -> None:
        self.phone.append((pcm, rate))


def _bus_chunk(rate: int, sample: int = 12000) -> bytes:
    n = rate // 50
    frame = array.array("h", [sample, sample] * n)
    return frame.tobytes()


class TestToneTests(unittest.IsolatedAsyncioTestCase):
    async def _run(
        self, chunks: int, *, flag: str | None
    ) -> tuple[hub.PipewireBridge, _Writer, _Tap, dict]:
        tap = _Tap()
        bridge = hub.PipewireBridge("sink", "mon", 8000, tap=tap)
        writer = _Writer()
        bridge._record = type("Rec", (), {"stdout": _Stdout([_bus_chunk(8000)] * chunks), "returncode": 0})()
        bridge._out_writer = writer
        env = {}
        if flag is not None:
            env["GSM2COMPUTER_TEST_TONE"] = flag
        previous = hub.open_call_tap
        hub.open_call_tap = {"id": tap.call_id, "dir": "/tmp/phone-tone", "meta": {}}
        try:
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("GSM2COMPUTER_TEST_TONE", None)
                if flag is not None:
                    os.environ["GSM2COMPUTER_TEST_TONE"] = flag
                await bridge._pump_out()
            opened = dict(hub.open_call_tap["meta"])
        finally:
            hub.open_call_tap = previous
        return bridge, writer, tap, opened

    def _events(self, writer: _Writer) -> list[dict]:
        return [json.loads(_ws_text(frame)) for frame in writer.sent]

    async def test_flag_off_sends_bus_audio_immediately(self) -> None:
        for flag in (None, "0"):
            with self.subTest(flag=flag):
                _bridge, writer, tap, opened = await self._run(3, flag=flag)
                self.assertNotIn("test_tone", opened)
                events = self._events(writer)
                self.assertEqual([e["seq"] for e in events], [1, 2, 3])
                self.assertNotIn("test_tone", tap.meta)
                for event in events:
                    pcm = audioop.ulaw2lin(base64.b64decode(event["delta"]), 2)
                    samples = array.array("h", pcm)
                    self.assertGreater(min(samples), 8000)
                    self.assertLess(max(samples) - min(samples), 800)
                self.assertEqual(len(tap.phone), 3)

    async def test_flag_on_sends_two_pulses_before_bus_audio(self) -> None:
        with self.assertLogs("gsm2computer-hub", level="INFO") as logs:
            bridge, writer, tap, opened = await self._run(101, flag="1")
        events = self._events(writer)
        self.assertEqual(len(events), 101)
        self.assertEqual([e["seq"] for e in events], list(range(1, 102)))
        self.assertEqual(bridge.dl_seq, 101)
        tone = events[:100]
        bus = events[100]
        self.assertTrue(all(e["type"] == "response.output_audio.delta" for e in tone))
        self.assertTrue(all(e["format"] == "audio/pcmu" and e["rate"] == 8000 for e in tone))
        kinds = []
        for event in tone:
            raw = base64.b64decode(event["delta"])
            self.assertEqual(len(raw), 160)
            kinds.append(_kind(audioop.ulaw2lin(raw, 2)))
        self.assertEqual(kinds[:13], ["on"] * 12 + ["edge"])
        self.assertEqual(kinds[13:25], ["off"] * 12)
        self.assertEqual(kinds[25:38], ["on"] * 12 + ["edge"])
        self.assertEqual(kinds[38:], ["off"] * 62)
        on = audioop.ulaw2lin(base64.b64decode(tone[0]["delta"]), 2)
        peak = max(abs(s) for s in array.array("h", on))
        self.assertGreater(peak, 6000)
        self.assertLess(peak, 12000)
        bus_pcm = audioop.ulaw2lin(base64.b64decode(bus["delta"]), 2)
        bus_samples = array.array("h", bus_pcm)
        self.assertGreater(min(bus_samples), 8000)
        self.assertEqual(tap.meta.get("test_tone"), True)
        self.assertEqual(opened.get("test_tone"), True)
        self.assertEqual(tap.phone[0][1], 8000)
        self.assertEqual(tap.phone[0][0][:4], hub.build_test_tone_pcm()[:4])
        self.assertEqual(len(tap.phone), 101)
        text = "\n".join(logs.output)
        self.assertIn("downlink test tone start", text)
        self.assertIn("downlink test tone end frames=100", text)


if __name__ == "__main__":
    unittest.main()
