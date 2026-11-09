"""Phone address for the hub-side call watcher."""
from __future__ import annotations

import unittest

import hub_call_watch


class PhonePeerTests(unittest.TestCase):
    def test_moto_ts_ip_wins(self) -> None:
        env = {
            "MOTO_TS_IP": "192.0.2.8",
            "GSM2COMPUTER_CALL_END_ADB": "192.0.2.9:5555",
        }
        self.assertEqual(hub_call_watch.phone_peer(env), "192.0.2.8")

    def test_host_of_call_end_adb(self) -> None:
        env = {"GSM2COMPUTER_CALL_END_ADB": "192.0.2.9:5555"}
        self.assertEqual(hub_call_watch.phone_peer(env), "192.0.2.9")

    def test_bare_host(self) -> None:
        self.assertEqual(hub_call_watch.host_of_adb("192.0.2.9"), "192.0.2.9")

    def test_bracketed_ipv6_host(self) -> None:
        self.assertEqual(hub_call_watch.host_of_adb("[2001:db8::8]:5555"), "2001:db8::8")

    def test_neither_is_empty(self) -> None:
        self.assertEqual(hub_call_watch.phone_peer({}), "")


if __name__ == "__main__":
    unittest.main()
