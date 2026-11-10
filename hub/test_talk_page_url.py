"""Offline regression tests: never attach to production Chromium."""
import asyncio
import importlib.util
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from talk_chromium import (
    CdpSession,
    OpenClawTalkUI,
    TalkUiError,
    _swallow_task,
    cdp_http,
    on_chat_main,
    page_ready,
)

ORIGIN = "https://hub-cup.mining-ling.ts.net"
GOOD = {"url": ORIGIN + "/chat/main", "hasTalkButton": True}
BAD = {"url": ORIGIN + "/dashboard/main", "hasTalkButton": True}
spec = importlib.util.spec_from_file_location("watchdog", Path(__file__).resolve().parent / "ops/watchdog/cup_watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class PageURLTests(unittest.TestCase):
    def test_exact_origin_and_path(self):
        for suffix in ("", "?x=1", "#fragment"):
            self.assertTrue(on_chat_main(GOOD["url"] + suffix))
        for url in (BAD["url"], ORIGIN + "/chat/other", ORIGIN + "/chat/main/",
                    ORIGIN + "/dashboard/main?next=/chat/main", "https://evil.test/chat/main",
                    "http://hub-cup.mining-ling.ts.net/chat/main", ORIGIN + ":444/chat/main",
                    "https://hub-cup.mining-ling.ts.net.evil.test/chat/main", "", None,
                    "https://[invalid/chat/main", "https://user@hub-cup.mining-ling.ts.net/chat/main"):
            with self.subTest(url=url):
                self.assertFalse(on_chat_main(url))
                self.assertFalse(page_ready({"url": url, "hasTalkButton": True}))
                ctx = watchdog.Ctx({}, None)
                ctx.health = {"talk": {"cdp": True, "page": {"url": url, "hasTalkButton": True}}}
                self.assertFalse(watchdog.check_talk_page(ctx)[0])
        self.assertFalse(page_ready({"url": GOOD["url"]}))

    def test_watchdog_good_and_busy(self):
        ctx = watchdog.Ctx({}, None)
        ctx.health = {"talk": {"cdp": True, "page": GOOD}}
        self.assertTrue(watchdog.check_talk_page(ctx)[0])
        ctx.busy = True
        self.assertIsNone(watchdog.check_talk_page(ctx)[0])


class HealTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ui = OpenClawTalkUI()
        self.ui.idle_check = lambda: True
        self.session = AsyncMock()
        self.session.evaluate.return_value = dict(BAD)

    async def test_idle_navigate_and_backoff(self):
        self.assertEqual(await self.ui._heal_page_url(self.session, BAD), "navigation requested")
        self.session.call.assert_awaited_once_with("Page.navigate", {"url": GOOD["url"]})
        self.assertEqual(await self.ui._heal_page_url(self.session, BAD), "backoff")
        self.assertFalse(self.ui.page_heal_in_progress)

    async def test_busy_active_live_and_unknown_block(self):
        for guard, active, page in ((lambda: False, False, BAD), (None, False, BAD),
                                   (lambda: True, True, BAD),
                                   (lambda: True, False, {**BAD, "live": True}),
                                   (lambda: True, False, {**BAD, "pcs": [{"connection": "connecting"}]})):
            self.ui.idle_check, self.ui.talk_active = guard, active
            self.assertIn("blocked", await self.ui._heal_page_url(self.session, page))
        self.session.call.assert_not_awaited()

    async def test_call_claims_during_navigate_aborts_the_load(self):
        started = asyncio.Event()

        async def call(method, params=None, timeout_s=5.0):
            if method == "Page.navigate":
                started.set()
                self.ui.idle_check = lambda: False
                await asyncio.sleep(5)
                raise AssertionError("navigate kept running after the call claimed")
            if method == "Page.stopLoading":
                return {}
            raise AssertionError(method)

        self.session.call.side_effect = call
        result = await asyncio.wait_for(self.ui._heal_page_url(self.session, BAD), timeout=2)
        self.assertTrue(started.is_set())
        self.assertEqual(result, "aborted: call claimed during navigate")
        self.assertFalse(self.ui.page_heal_in_progress)
        methods = [c.args[0] for c in self.session.call.await_args_list]
        self.assertEqual(["Page.navigate", "Page.stopLoading"], methods)

    async def test_navigate_that_returns_while_a_call_is_waiting_stops_loading(self):
        async def call(method, params=None, timeout_s=5.0):
            if method == "Page.navigate":
                self.ui.note_pending_admission(True)
                return {}
            if method == "Page.stopLoading":
                return {}
            raise AssertionError(method)

        self.session.call.side_effect = call
        result = await self.ui._heal_page_url(self.session, BAD)
        self.assertEqual(result, "aborted: call claimed during navigate")
        self.assertTrue(self.ui.idle_check())
        self.assertFalse(self.ui.page_heal_in_progress)
        methods = [c.args[0] for c in self.session.call.await_args_list]
        self.assertEqual(["Page.navigate", "Page.stopLoading"], methods)

    async def test_probe_cancel_stops_loading_while_a_call_is_waiting(self):
        """The health probe is cancelled. The idle callback stays true.

        Admission is only a pending note, so the heal must not be the idle-poll
        abort. stopLoading has to run before page_heal_in_progress clears.
        """
        started = asyncio.Event()
        release = asyncio.Event()
        stopped_while_healing = asyncio.Event()

        async def call(method, params=None, timeout_s=5.0):
            if method == "Page.navigate":
                started.set()
                await release.wait()
                return {}
            if method == "Page.stopLoading":
                if self.ui.page_heal_in_progress:
                    stopped_while_healing.set()
                return {}
            raise AssertionError(method)

        self.session.call.side_effect = call
        self.ui._connect_page = AsyncMock(return_value=self.session)
        probe = asyncio.create_task(self.ui._health_page_probe())
        await asyncio.wait_for(started.wait(), timeout=2)
        self.ui.note_pending_admission(True)
        probe.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(probe, timeout=2)
        release.set()
        self.assertTrue(self.ui.idle_check())
        self.assertEqual(1, self.ui.pending_admissions)
        self.assertFalse(self.ui.page_heal_in_progress)
        self.assertTrue(stopped_while_healing.is_set())
        methods = [c.args[0] for c in self.session.call.await_args_list]
        self.assertEqual(["Page.navigate", "Page.stopLoading"], methods)

    async def test_cancelled_cdp_call_drops_pending(self):
        class HungWs:
            async def send(self, data: str) -> None:
                return None

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(3600)
                raise StopAsyncIteration

            async def close(self) -> None:
                return None

        session = CdpSession(HungWs())
        try:
            task = asyncio.create_task(session.call("Page.navigate", {"url": GOOD["url"]}))
            await asyncio.sleep(0.05)
            self.assertEqual(1, len(session._pending))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual({}, session._pending)
        finally:
            await session.close()

    async def test_swallow_does_not_adopt_a_cancelled_child(self):
        async def child() -> None:
            await asyncio.sleep(30)

        task = asyncio.create_task(child())
        task.cancel()
        await _swallow_task(task)
        await asyncio.sleep(0)

    async def test_swallow_reraises_when_this_task_is_cancelled(self):
        async def child() -> None:
            await asyncio.sleep(30)

        child_task = asyncio.create_task(child())

        async def parent() -> None:
            await _swallow_task(child_task)

        parent_task = asyncio.create_task(parent())
        await asyncio.sleep(0)
        parent_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent_task
        child_task.cancel()
        await asyncio.gather(child_task, return_exceptions=True)

    async def test_call_arrives_during_probe(self):
        async def evaluate(*args):
            self.ui.idle_check = lambda: False
            return BAD
        self.session.evaluate.side_effect = evaluate
        self.assertIn("blocked", await self.ui._heal_page_url(self.session, BAD))
        self.session.call.assert_not_awaited()
        # Aborted before navigate must not burn the 120s cooldown.
        self.ui.idle_check = lambda: True
        self.session.evaluate.side_effect = None
        self.session.evaluate.return_value = dict(BAD)
        self.assertEqual(await self.ui._heal_page_url(self.session, BAD), "navigation requested")

    async def test_failed_navigation_keeps_backoff(self):
        self.session.call.side_effect = RuntimeError("CDP failed")
        with self.assertRaises(RuntimeError):
            await self.ui._heal_page_url(self.session, BAD)
        self.assertFalse(self.ui.page_heal_in_progress)
        self.assertEqual(await self.ui._heal_page_url(self.session, BAD), "backoff")

    async def test_ensure_does_not_accept_dashboard_button(self):
        self.ui.idle_check = lambda: False
        with self.assertRaises(TalkUiError):
            await self.ui._ensure_control_ui(self.session)
        self.session.call.assert_not_awaited()

    async def test_ensure_navigates_then_waits_for_exact_url(self):
        self.session.evaluate.side_effect = [BAD, BAD, GOOD]
        with patch("talk_chromium.asyncio.sleep", new_callable=AsyncMock):
            await self.ui._ensure_control_ui(self.session)
        self.session.call.assert_awaited_once_with("Page.navigate", {"url": GOOD["url"]})

    async def test_health_exposes_reason_and_readonly_probe(self):
        self.ui.idle_check = lambda: False
        self.ui._connect_page = AsyncMock(return_value=self.session)
        result = await self.ui._health_page_probe()
        self.assertFalse(result["onChatMain"])
        self.assertIn("wrong origin or path", result["reason"])
        self.ui._connect_page.assert_awaited_once_with(read_only=True)
        self.session.close.assert_awaited_once()

    async def test_reload_unknown_idle_is_blocked(self):
        self.ui.idle_check = None
        with self.assertRaises(TalkUiError):
            await self.ui.reload_control_ui()

    async def test_snapshot_does_not_touch_cdp_or_navigate(self):
        def blocked(*_args, **_kwargs):
            raise AssertionError("CDP HTTP on the health snapshot")

        self.session.call = AsyncMock(side_effect=AssertionError("navigate on snapshot"))
        with patch("talk_chromium._cdp_http", side_effect=blocked):
            snap = self.ui.health_snapshot()
        self.assertIsNone(snap["cdp"])
        self.assertIsNone(snap["age_s"])
        self.assertIsNone(snap["page"])
        self.assertIn("url", snap)
        self.session.call.assert_not_awaited()

    async def test_cdp_http_stays_off_the_event_loop(self):
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            for _ in range(8):
                await asyncio.sleep(0.02)
                ticks += 1

        def blocked(path: str, timeout_s: float = 2.0, method: str = "GET"):
            time.sleep(0.25)
            return {"Browser": "test", "path": path, "method": method}

        with patch("talk_chromium._cdp_http", side_effect=blocked):
            ticker = asyncio.create_task(tick())
            await cdp_http("/json/version", timeout_s=1.0)
            self.assertGreaterEqual(ticks, 5)
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

    async def test_refresh_heals_off_the_snapshot(self):
        self.ui._connect_page = AsyncMock(return_value=self.session)
        with patch("talk_chromium.cdp_ready", new=AsyncMock(return_value=True)), patch(
            "talk_chromium.browser_pid", return_value=42
        ):
            before = self.ui.health_snapshot()
            self.assertIsNone(before["page"])
            await self.ui._refresh_cached_health()
        self.session.call.assert_awaited_once_with("Page.navigate", {"url": GOOD["url"]})
        after = self.ui.health_snapshot()
        self.assertEqual(after["browser_pid"], 42)
        self.assertTrue(after["cdp"])
        self.assertFalse(after["page"]["onChatMain"])
        self.assertEqual(after["page"]["heal"], "navigation requested")
        self.assertIsNotNone(after["age_s"])
        self.assertLess(after["age_s"], 5)


if __name__ == "__main__":
    unittest.main()
