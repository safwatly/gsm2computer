#!/usr/bin/env python3
"""Talk session rollover watchdog (short GSM2COMPUTER_TALK_ROLLOVER_S)."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

# hub.py reads rollover cap at import time
os.environ["GSM2COMPUTER_TALK_ROLLOVER_S"] = "0.05"

import hub as hub_mod  # noqa: E402


class _FakeBridge:
    def __init__(self) -> None:
        self._closed = False
        self._abort = False
        self._abort_reason: str | None = None

    def aborted(self) -> bool:
        return self._abort

    def _signal_abort(self, reason: str) -> None:
        self._abort = True
        self._abort_reason = reason


class _FakeOpenClaw:
    def __init__(self) -> None:
        self._closed = False
        self.talk_active = True
        self.talk_started_at = 0.0
        self.talk_rollover_in_progress = False
        self.restart_talk = AsyncMock()

    async def _age_talk(self) -> None:
        self.talk_started_at = hub_mod.time.monotonic() - hub_mod.TALK_ROLLOVER_S - 0.01


class TalkRolloverWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fires_restart_when_cap_elapsed(self) -> None:
        oc = _FakeOpenClaw()
        await oc._age_talk()
        br = _FakeBridge()

        async def _once() -> None:
            oc.talk_started_at = hub_mod.time.monotonic()
            br._closed = True

        oc.restart_talk.side_effect = _once
        await asyncio.wait_for(hub_mod._talk_rollover_watch(oc, br), timeout=3.0)
        oc.restart_talk.assert_awaited_once()
        self.assertFalse(br.aborted())

    async def test_keeps_watching_through_rollover_flag(self) -> None:
        oc = _FakeOpenClaw()
        await oc._age_talk()
        br = _FakeBridge()

        async def _restart() -> None:
            oc.talk_rollover_in_progress = True
            oc.talk_active = False
            await asyncio.sleep(0.15)
            oc.talk_active = True
            oc.talk_started_at = hub_mod.time.monotonic()
            oc.talk_rollover_in_progress = False
            br._closed = True

        oc.restart_talk.side_effect = _restart
        await asyncio.wait_for(hub_mod._talk_rollover_watch(oc, br), timeout=3.0)
        oc.restart_talk.assert_awaited()
        self.assertFalse(br.aborted())

    async def test_abort_on_restart_failure(self) -> None:
        oc = _FakeOpenClaw()
        await oc._age_talk()
        br = _FakeBridge()
        oc.restart_talk.side_effect = RuntimeError("cdp down")
        await hub_mod._talk_rollover_watch(oc, br)
        self.assertTrue(br.aborted())
        self.assertIn("cdp down", br._abort_reason or "")


if __name__ == "__main__":
    unittest.main()
