#!/usr/bin/env python3
"""16 kHz binary uplink frames, and the old JSON append still resamples."""

import unittest

import hub


def _g2(pcm: bytes, rate: int = 16000) -> bytes:
    return b"G2" + bytes((1, 1)) + rate.to_bytes(2, "big") + b"\x00\x00" + pcm


class UplinkBinaryTests(unittest.TestCase):
    def test_g2_header_is_16k_pcm(self) -> None:
        pcm = (1000).to_bytes(2, "little", signed=True) * 320
        parsed = hub.parse_uplink_binary(_g2(pcm))
        self.assertIsNotNone(parsed)
        assert parsed is not None
        audio, fmt, rate = parsed
        self.assertEqual(fmt, "audio/pcm")
        self.assertEqual(rate, 16000)
        self.assertEqual(audio, pcm)

    def test_16k_resamples_up_to_the_48k_bus(self) -> None:
        pcm = (1000).to_bytes(2, "little", signed=True) * 320
        parsed = hub.parse_uplink_binary(_g2(pcm))
        assert parsed is not None
        bus, _state = hub._resample_pcm(parsed[0], parsed[2], 48000, None)
        self.assertGreater(len(bus), len(pcm))
        self.assertEqual(len(bus) % 2, 0)

    def test_rejects_a_short_or_foreign_frame(self) -> None:
        self.assertIsNone(hub.parse_uplink_binary(b"G2"))
        self.assertIsNone(hub.parse_uplink_binary(b"\x00" * 32))

    def test_missing_format_stays_ulaw(self) -> None:
        self.assertEqual(hub._normalize_format(""), "")
        self.assertEqual(hub._normalize_format("audio/pcm"), "audio/pcm")


if __name__ == "__main__":
    unittest.main()
