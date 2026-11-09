"""Post-call Grok transcript and always-on phone downlink recording."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import call_tap  # noqa: E402
import grokbot_transcript as gt  # noqa: E402
from call_tap import PHONE_DOWNLINK, CallTap, _prune  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_nodes_become_caller_and_assistant_turns(self) -> None:
        doc = gt.normalize_turns(
            [
                {"testid": "user-message", "text": "  Hello Suzie  ", "timestamp": "0:01"},
                {"role": "Assistant", "text": "Hi.", "timestamp": None},
                {"speaker": "caller", "text": "", "timestamp": "0:02"},
                "skip",
            ],
            "20261109T120000Z-grokbot",
        )
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["call_id"], "20261109T120000Z-grokbot")
        self.assertEqual(
            doc["turns"],
            [
                {"speaker": "caller", "text": "Hello Suzie", "timestamp": "0:01"},
                {"speaker": "assistant", "text": "Hi.", "timestamp": None},
            ],
        )

    def test_empty_is_not_ok(self) -> None:
        doc = gt.normalize_turns({"turns": []}, "call-1")
        self.assertEqual(doc["status"], "empty")
        self.assertEqual(doc["turns"], [])

    def test_numeric_timestamp_kept(self) -> None:
        doc = gt.normalize_turns([{"speaker": "assistant", "text": "Yes", "timestamp": 1.5}], "c")
        self.assertEqual(doc["turns"][0]["timestamp"], 1.5)


class SaveTests(unittest.TestCase):
    def test_round_trip_pointer_and_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pointer = gt.save_transcript_result(
                directory,
                "20261109T120000Z-grokbot",
                "ok",
                [{"speaker": "caller", "text": "Hi", "timestamp": "0:01"}],
            )
            saved = json.loads((directory / "transcript.json").read_text())
            self.assertEqual(saved["call_id"], "20261109T120000Z-grokbot")
            self.assertEqual(saved["turns"][0]["speaker"], "caller")
            self.assertEqual(pointer["turns"], 1)
            self.assertEqual(pointer["preview"][0]["text"], "Hi")
            (directory / "levels.json").write_text("{}\n")
            gt.attach_levels(directory, pointer)
            levels = json.loads((directory / "levels.json").read_text())
            self.assertEqual(levels["transcript"]["status"], "ok")
            self.assertEqual(gt.load_saved_pointer(directory)["file"], "transcript.json")

    def test_failure_is_a_file_not_an_exception(self) -> None:
        async def boom(_url: str):
            raise RuntimeError("cdp list failed")

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pointer = asyncio.run(
                gt.capture_call_transcript(
                    directory,
                    "call-9",
                    cdp_http="http://127.0.0.1:9333",
                    read_turns=boom,
                )
            )
            self.assertEqual(pointer["status"], "error")
            body = json.loads((directory / "transcript.json").read_text())
            self.assertIn("cdp list failed", body["error"])
            self.assertEqual(body["turns"], [])

    def test_missing_cdp_does_not_call_the_reader(self) -> None:
        called = {"n": 0}

        async def reader(_url: str):
            called["n"] += 1
            return []

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"GSM2COMPUTER_GROKBOT_CDP": ""}, clear=False):
                pointer = asyncio.run(
                    gt.capture_call_transcript(
                        Path(tmp),
                        "call-9",
                        status={"cdp_up": True},
                        read_turns=reader,
                    )
                )
            self.assertEqual(pointer["status"], "no_cdp")
            self.assertEqual(called["n"], 0)


class CdpBaseTests(unittest.TestCase):
    def test_status_port_and_reject_public_host(self) -> None:
        with patch.dict(os.environ, {"GSM2COMPUTER_GROKBOT_CDP": ""}, clear=False):
            self.assertEqual(
                gt.cdp_base_from_status({"json": {"cdp_port": 9333}}),
                "http://127.0.0.1:9333",
            )
            self.assertEqual(
                gt.cdp_base_from_status({"cdp_http": "http://127.0.0.1:9333/json"}),
                "http://127.0.0.1:9333/json",
            )
            self.assertIsNone(gt.cdp_base_from_status({"cdp_http": "http://203.0.113.5:9333"}))
            self.assertIsNone(gt.cdp_base_from_status({"cdp_up": True}))

    def test_env_overrides_status(self) -> None:
        with patch.dict(os.environ, {"GSM2COMPUTER_GROKBOT_CDP": "http://127.0.0.1:9444"}):
            self.assertEqual(gt.cdp_base_from_status({"cdp_port": 1}), "http://127.0.0.1:9444")

    def test_page_must_be_grok(self) -> None:
        targets = [
            {"type": "page", "url": "http://127.0.0.1:18789/chat", "webSocketDebuggerUrl": "ws://talk"},
            {"type": "page", "url": "https://grok.com/", "webSocketDebuggerUrl": "ws://grok"},
        ]
        self.assertEqual(gt._grok_page(targets)["webSocketDebuggerUrl"], "ws://grok")
        self.assertIsNone(gt._grok_page(targets[:1]))


class DomJsTests(unittest.TestCase):
    def test_voice_dom_reads_both_sides_and_picks_the_session(self) -> None:
        script = r"""
const assert = require('assert');
%s
class El {
  constructor(attrs, text, children) {
    this.attrs = attrs || {};
    this.text = text || '';
    this.children = children || [];
    this.parent = null;
    for (const child of this.children) child.parent = this;
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  get classList() { return { contains: (c) => String(this.attrs.class || '').split(/\s+/).includes(c) }; }
  get innerText() { return this.text || this.children.map((c) => c.innerText).filter(Boolean).join(' '); }
  get textContent() { return this.innerText; }
  querySelectorAll(sel) { return this._all().filter((el) => el !== this && match(el, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  _all() { return [this, ...this.children.flatMap((c) => c._all())]; }
  contains(other) { return this._all().includes(other); }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
  cloneNode() {
    const copy = new El({...this.attrs}, this.text, this.children.map((c) => c.cloneNode()));
    return copy;
  }
  getBoundingClientRect() { return { width: 10, height: 10 }; }
}
function match(el, sel) {
  if (sel === '.sr-only, .thinking-container, time') {
    return el.attrs.tag === 'time' || String(el.attrs.class || '').split(/\s+/).some((c) => c === 'sr-only' || c === 'thinking-container');
  }
  if (sel === 'time') return el.attrs.tag === 'time';
  if (sel === '[data-testid="user-message"], [data-testid="assistant-message"], div.message-bubble') {
    const id = el.attrs['data-testid'];
    return id === 'user-message' || id === 'assistant-message' || String(el.attrs.class || '').split(/\s+/).includes('message-bubble');
  }
  if (sel === 'a[href^="/c/"]') return String(el.attrs.href || '').startsWith('/c/');
  return false;
}
const root = new El({}, '', [
  new El({ class: 'message-bubble' }, '', [
    new El({ 'data-testid': 'user-message' }, '', [
      new El({ class: 'sr-only' }, 'You', []),
      new El({ tag: 'time', datetime: '0:02' }, '0:02', []),
      new El({}, 'Need a ride', []),
    ]),
  ]),
  new El({ 'data-testid': 'assistant-message', 'data-timestamp': '0:04' }, 'On the way', []),
  new El({ href: '/c/old', 'aria-label': 'Start voice' }, 'Start voice', []),
  new El({ href: '/c/voice-1', 'aria-label': 'Voice call with Suzie' }, 'Voice call', []),
  new El({ href: '/c/text-1' }, 'Notes', []),
]);
const turns = gsm2VoiceTurns(root);
assert.strictEqual(turns.length, 2);
assert.strictEqual(turns[0].speaker, 'caller');
assert.strictEqual(turns[0].text, 'Need a ride');
assert.strictEqual(turns[0].timestamp, '0:02');
assert.strictEqual(turns[1].speaker, 'assistant');
assert.strictEqual(turns[1].text, 'On the way');
assert.strictEqual(turns[1].timestamp, '0:04');
assert.strictEqual(gsm2VoiceSessionHref(root), '/c/voice-1');
""" % gt.VOICE_DOM_JS
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])


class DownlinkTests(unittest.TestCase):
    def test_resample_to_8k_and_keep_after_other_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tap = CallTap("20261109T120000Z-openclaw", {"mode": "webrtc-ui"}, record_dir=root)
            pcm = b"\x00\x10" * 480
            tap.write_phone_downlink(pcm, 48000)
            tap.write_phone_downlink(pcm, 48000)
            tap.write_phone_downlink(b"\x00", 8000)
            for stream in tap._streams.values():
                stream.close()
            path = tap.dir / f"{PHONE_DOWNLINK}.wav"
            with wave.open(str(path), "rb") as wav:
                self.assertEqual(wav.getframerate(), 8000)
                self.assertEqual(wav.getnchannels(), 1)
                self.assertGreater(wav.getnframes(), 0)
            mixed = set()
            for _out, left, right, _rate in call_tap._ROOM_MIXES:
                mixed.add(left)
                mixed.add(right)
            self.assertNotIn(PHONE_DOWNLINK, mixed)

    def test_bridge_records_and_swallows_tap_errors(self) -> None:
        import hub

        class Boom:
            def write_phone_downlink(self, pcm: bytes, rate: int) -> None:
                raise OSError("disk full")

        bridge = hub.PipewireBridge("sink", "mon", 48000, tap=Boom())
        bridge.note_phone_downlink(b"\x00\x01\x00\x02", 8000)

        with tempfile.TemporaryDirectory() as tmp:
            tap = CallTap("id", {}, record_dir=Path(tmp))
            bridge.tap = tap
            bridge.note_phone_downlink(b"\x00\x10" * 8, 8000)
            stream = tap._streams[PHONE_DOWNLINK]
            stream.close()
            self.assertTrue((tap.dir / f"{PHONE_DOWNLINK}.wav").stat().st_size > 44)

    def test_both_send_paths_record(self) -> None:
        text = (HERE / "hub.py").read_text(encoding="utf-8")
        self.assertIn("self.note_phone_downlink(payload_pcm, out_rate)", text)
        self.assertIn("bridge.note_phone_downlink(", text)

    def test_prune_bounds_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(4):
                folder = root / f"2026110{i}T000000Z-openclaw"
                folder.mkdir()
                (folder / f"{PHONE_DOWNLINK}.wav").write_bytes(b"RIFFxxxx")
            _prune(root, 2)
            left = sorted(p.name for p in root.iterdir())
            self.assertEqual(len(left), 2)
            self.assertTrue(all("20261103" in name or "20261102" in name for name in left))


class AlertAndCallLogTests(unittest.TestCase):
    def test_call_ended_points_at_transcript(self) -> None:
        import call_end_watch as cew

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            gt.save_transcript_result(
                directory,
                "20261109T120000Z-grokbot",
                "ok",
                [
                    {"speaker": "caller", "text": "Hello", "timestamp": "0:01"},
                    {"speaker": "assistant", "text": "Hi there", "timestamp": "0:03"},
                ],
            )
            health = {
                "ok": True,
                "call": {"busy": False},
                "talk": {},
                "last_call_tap": {"id": "20261109T120000Z-grokbot", "dir": str(directory), "meta": {"mode": "grokbot"}},
            }
            st = cew.WatchState(call_id="phone-1", number="+15551212")
            with patch.object(cew, "journal_summary", return_value={}):
                payload = cew.build_payload(st=st, duration_s=30, test=True, health=health, now_ts=1_700_000_000)
            self.assertEqual(payload["transcript"]["status"], "ok")
            self.assertEqual(payload["transcript"]["turns"], 2)
            self.assertEqual(payload["transcript"]["preview"][0]["speaker"], "caller")
            self.assertIn("transcript_status=ok", payload["text"])
            self.assertIn("transcript.json", payload["transcript"]["path"])

    def test_openclaw_alert_omits_transcript(self) -> None:
        import call_end_watch as cew

        health = {"ok": True, "call": {}, "talk": {}, "last_call_tap": {"id": "20261109T120000Z-webrtc-ui", "meta": {"mode": "webrtc-ui"}}}
        st = cew.WatchState(call_id="phone-1")
        with patch.object(cew, "journal_summary", return_value={}):
            payload = cew.build_payload(st=st, duration_s=5, test=True, health=health, now_ts=1_700_000_000)
        self.assertNotIn("transcript", payload)

    def test_hub_watch_and_portal_list(self) -> None:
        import hub_call_watch
        from portal_http import _annotate_call
        from portal_store import PortalStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = root / "calls"
            folder = calls / "20261109T120000Z-grokbot"
            folder.mkdir(parents=True)
            gt.save_transcript_result(folder, folder.name, "empty", error=None)
            with patch.dict(os.environ, {"GSM2COMPUTER_CALL_RECORD_DIR": str(calls)}):
                pointer = hub_call_watch.transcript_pointer(folder.name)
            self.assertEqual(pointer["status"], "empty")
            self.assertIsNone(hub_call_watch.transcript_pointer("../secret"))

            store = PortalStore(root / "portal.sqlite")
            try:
                item = store.add_call("in", "+15550001111", "2026-11-09T12:00:00Z", 8, session_id=folder.name)
                annotated = _annotate_call(item, calls)
                self.assertEqual(annotated["transcript"]["status"], "empty")
                self.assertIsNone(_annotate_call({"session_id": "../secret"}, calls).get("transcript"))
                other = calls / "20261109T130000Z-loopback"
                other.mkdir()
                gt.save_transcript_result(
                    other,
                    other.name,
                    "ok",
                    turns=[{"speaker": "caller", "text": "wrong call", "timestamp": None}],
                )
                stale = _annotate_call(
                    {
                        "session_id": folder.name,
                        "tap_summary": json.dumps({"id": other.name, "dir": str(other)}),
                    },
                    calls,
                )
                self.assertEqual(stale["transcript"]["call_id"], folder.name)
                self.assertEqual(stale["transcript"]["status"], "empty")
            finally:
                store.close()


class StopLaterHookTests(unittest.TestCase):
    def test_hook_runs_after_off_and_a_failed_hook_is_swallowed(self) -> None:
        from test_grokbot_mode import FakePatchbay, voice

        pb = FakePatchbay()
        v = voice(pb)

        async def scenario() -> None:
            seen: dict[str, object] = {}

            async def hook(res: dict) -> None:
                seen["calls"] = list(pb.calls())
                seen["ok"] = res["ok"]

            await v.start()
            await v.stop_later(on_stopped=hook)
            self.assertEqual(seen["calls"], ["switch suzie --json", "off --json"])
            self.assertTrue(seen["ok"])

            async def bad(_res: dict) -> None:
                raise RuntimeError("transcript blew up")

            await v.start()
            await v.stop_later(on_stopped=bad)

        asyncio.run(scenario())
        self.assertEqual(pb.calls().count("off --json"), 2)


if __name__ == "__main__":
    unittest.main()
