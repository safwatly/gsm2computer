"""Post-call Grok transcript and always-on phone downlink recording."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from datetime import datetime, timezone
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
                {"speaker": "unknown", "text": "mystery bubble"},
                {"role": "narrator", "text": "stage direction"},
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

    def test_page_must_be_grok_on_loopback(self) -> None:
        targets = [
            {"type": "page", "url": "http://127.0.0.1:18789/chat", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"},
            {"type": "page", "url": "https://notgrok.com/", "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/9"},
            {"type": "page", "url": "https://grok.com/", "webSocketDebuggerUrl": "ws://203.0.113.5:9333/devtools/page/1"},
            {"type": "page", "url": "https://grok.com/c/abc", "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/2"},
        ]
        self.assertEqual(gt._grok_page(targets)["webSocketDebuggerUrl"], "ws://127.0.0.1:9333/devtools/page/2")
        self.assertIsNone(gt._grok_page(targets[:1]))
        self.assertFalse(gt._is_grok_url("https://notgrok.com/c/abc"))
        self.assertFalse(gt._is_grok_url("https://evil.example/?next=https://grok.com"))
        self.assertTrue(gt._is_grok_url("https://grok.com/c/abc"))
        self.assertTrue(gt._is_grok_url("https://www.grok.com/"))
        self.assertTrue(gt._is_grok_url("https://x.com/i/grok"))
        self.assertFalse(gt._is_grok_url("https://x.com/home"))
        self.assertFalse(gt._loopback_ws("ws://203.0.113.5:9333/devtools/page/1"))
        self.assertTrue(gt._loopback_ws("ws://127.0.0.1:9333/devtools/page/2"))

    def test_desktop_renderer_url_is_a_grok_page_on_loopback_only(self) -> None:
        live = "file:///opt/Grok%20Bot/resources/app.asar/dist/renderer/index.html"
        self.assertTrue(gt._is_desktop_renderer_url(live))
        self.assertTrue(
            gt._is_desktop_renderer_url(
                "file:///opt/Grok Bot/resources/app.asar/dist/renderer/index.html"
            )
        )
        self.assertFalse(gt._is_grok_url(live))
        self.assertFalse(gt._is_desktop_renderer_url("file:///tmp/index.html"))
        self.assertFalse(gt._is_desktop_renderer_url("file:///etc/passwd"))
        self.assertFalse(
            gt._is_desktop_renderer_url(
                "file:///opt/Grok%20Bot/resources/app.asar/dist/renderer/other.html"
            )
        )
        self.assertFalse(
            gt._is_desktop_renderer_url(
                "file:///opt/Grok%20Bot/resources/app.asar/dist/renderer/index.html.bak"
            )
        )
        self.assertFalse(
            gt._is_desktop_renderer_url(
                "https://evil.example/resources/app.asar/dist/renderer/index.html"
            )
        )
        page = {
            "type": "page",
            "url": live,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9230/devtools/page/1",
        }
        self.assertEqual(gt._grok_page([page])["webSocketDebuggerUrl"], page["webSocketDebuggerUrl"])
        public = dict(page, webSocketDebuggerUrl="ws://203.0.113.5:9230/devtools/page/1")
        self.assertIsNone(gt._grok_page([public]))
        other = dict(page, url="file:///tmp/notes.html")
        self.assertIsNone(gt._grok_page([other]))
        src = inspect.getsource(gt.read_voice_turns)
        self.assertLess(src.index("_is_desktop_renderer_url"), src.index("select_voice_session"))


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
  new El({ class: 'message-bubble' }, 'mystery bubble', []),
  new El({ href: '/c/old', 'aria-label': 'Start voice' }, 'Start voice', []),
  new El({ href: '/c/voice-1', 'aria-label': 'Voice call with Suzie', 'data-started': '2026-11-09T12:00:30Z' }, 'Voice call', []),
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
const sessions = gsm2VoiceSessions(root);
assert.deepStrictEqual(sessions.map((s) => s.href), ['/c/voice-1']);
assert.strictEqual(sessions[0].started, '2026-11-09T12:00:30Z');
""" % gt.VOICE_DOM_JS
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

    def test_desktop_renderer_dom_is_not_the_grok_com_selectors(self) -> None:
        script = r"""
const assert = require('assert');
%s
class El {
  constructor(attrs, text, children) {
    this.attrs = attrs || {};
    this.text = text || '';
    this.children = children || [];
    this.parent = null;
    this.tagName = this.attrs.tag ? String(this.attrs.tag).toUpperCase() : '';
    for (const child of this.children) child.parent = this;
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  get innerText() {
    const parts = [];
    if (this.text) parts.push(this.text);
    for (const child of this.children) {
      const value = child.innerText;
      if (value) parts.push(value);
    }
    return parts.join(' ');
  }
  get textContent() { return this.innerText; }
  querySelectorAll(sel) { return this._all().filter((el) => el !== this && match(el, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  _all() { return [this, ...this.children.flatMap((c) => c._all())]; }
  contains(other) { return this._all().includes(other); }
  cloneNode() { return new El({...this.attrs}, this.text, this.children.map((c) => c.cloneNode())); }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
}
function hasClass(el, name) {
  return String(el.attrs.class || '').split(/\s+/).includes(name);
}
function match(el, sel) {
  if (sel.includes(',')) return sel.split(',').some((part) => match(el, part.trim()));
  if (sel === '*') return true;
  if (sel === '[aria-label]') return Object.prototype.hasOwnProperty.call(el.attrs, 'aria-label');
  if (sel === '[aria-label^="Message actions"]') return String(el.attrs['aria-label'] || '').startsWith('Message actions');
  if (sel === '[aria-label="Your answer"]') return el.attrs['aria-label'] === 'Your answer';
  if (sel === '[aria-label="Voice chat feedback"]') return el.attrs['aria-label'] === 'Voice chat feedback';
  if (sel === '.sand-row-timestamp') return hasClass(el, 'sand-row-timestamp');
  if (sel === 'time' || sel === 'button') return String(el.attrs.tag || '') === sel;
  if (sel === '[role="log"][aria-label="Conversation transcript"]') {
    return el.attrs.role === 'log' && el.attrs['aria-label'] === 'Conversation transcript';
  }
  if (sel === 'article.sand-transcript-row') return el.attrs.tag === 'article' && hasClass(el, 'sand-transcript-row');
  if (sel === 'article.sand-virtual-transcript__row') return el.attrs.tag === 'article' && hasClass(el, 'sand-virtual-transcript__row');
  if (sel === '[role="article"].sand-transcript-row') return el.attrs.role === 'article' && hasClass(el, 'sand-transcript-row');
  if (sel === '[role="article"].sand-virtual-transcript__row') return el.attrs.role === 'article' && hasClass(el, 'sand-virtual-transcript__row');
  if (sel === '.sr-only' || sel === '.thinking-container') return false;
  if (sel === '[data-testid="user-message"]' || sel === '[data-testid="assistant-message"]' || sel === 'div.message-bubble') return false;
  if (sel === 'a[href^="/c/"]') return false;
  return false;
}
function row(speaker, body, actionsLabel, stamp) {
  return new El({ tag: 'article', role: 'article', class: 'sand-transcript-row sand-virtual-transcript__row', 'aria-label': '' }, '', [
    new El({ class: 'sand-transcript-speaker' }, speaker, []),
    new El({ tag: 'time', class: 'sand-row-timestamp' }, stamp, []),
    new El({ role: 'group', 'aria-label': actionsLabel }, actionsLabel, []),
    new El({ class: 'sand-transcript-body' }, body, []),
    new El({ role: 'group', 'aria-label': 'Your answer' }, 'Your answer', []),
    new El({ role: 'group', 'aria-label': 'Voice chat feedback' }, 'Voice chat feedback', []),
  ]);
}
const log = new El({ role: 'log', 'aria-label': 'Conversation transcript' }, '', [
  row('You', 'Need a ride. You should hurry.', 'Message actions for You message at 8:20:01 PM', '8:20 PM'),
  row('Suzie', 'On the way, and Suzie will text you.', 'Message actions for Suzie message at 8:21:06 PM', '8:21 PM'),
  new El({ tag: 'article', role: 'article', class: 'sand-virtual-transcript__row', 'aria-label': '' }, '', [
    new El({ class: 'sand-transcript-heading' }, 'Suzie', [
      new El({ tag: 'time', class: 'sand-row-timestamp' }, '8:22 PM', []),
    ]),
    new El({ role: 'group', 'aria-label': 'Message actions for Suzie message at 8:22:10 PM' }, 'Message actions for Suzie message at 8:22:10 PM', []),
    new El({ class: 'sand-transcript-body' }, 'See you soon', []),
  ]),
  new El({ tag: 'article', role: 'article', class: 'sand-transcript-row sand-virtual-transcript__row', 'aria-label': '' }, '', [
    new El({ class: 'sand-transcript-speaker' }, 'You', []),
    new El({ class: 'sand-transcript-body' }, 'Still here', []),
  ]),
  new El({ tag: 'article', role: 'article', class: 'sand-transcript-row sand-virtual-transcript__row', 'aria-label': '', 'data-role': 'user' }, '', [
    new El({ tag: 'time', class: 'sand-row-timestamp' }, '9:05 PM', []),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'You', []),
      new El({ class: 'sand-transcript-body' }, 'Hello.', []),
    ]),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'You', []),
      new El({ class: 'sand-transcript-body' }, 'Hi.', []),
    ]),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'Suzie', []),
      new El({ class: 'sand-transcript-body' }, 'Hey.', []),
    ]),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'You', []),
      new El({ class: 'sand-transcript-body' }, "I'm here.", []),
    ]),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'You', []),
      new El({ class: 'sand-transcript-body' }, "Where's", []),
    ]),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'Suzie', []),
      new El({ class: 'sand-transcript-body' }, 'Sounds like that got cut off. Go ahead.', []),
    ]),
  ]),
  new El({ tag: 'article', role: 'article', class: 'sand-transcript-row sand-virtual-transcript__row', 'aria-label': '', 'data-role': 'user' }, '', [
    new El({ tag: 'time', class: 'sand-row-timestamp' }, '8:18 PM', []),
    new El({ role: 'group', 'aria-label': 'Message actions for You message at 8:18:02 PM' }, 'Message actions for You message at 8:18:02 PM', []),
    new El({ 'data-role': 'user', class: 'sand-fill-bubble-user' }, '', [
      new El({ class: 'sand-transcript-speaker' }, 'You', []),
      new El({ class: 'sand-transcript-body' }, 'Hello', []),
    ]),
    new El({}, '', [
      new El({ class: 'sand-transcript-speaker' }, 'Suzie', []),
      new El({ class: 'sand-transcript-body' }, 'Hey Safwat.', []),
    ]),
    new El({ 'data-role': 'user', class: 'sand-fill-bubble-user' }, 'Yes, please.', []),
    new El({ 'data-role': 'assistant', class: 'sand-fill-bubble-agent' }, "I'm here. What can I do for you?", []),
    new El({ role: 'group', 'aria-label': 'Voice chat feedback' }, 'Voice chat feedback', []),
  ]),
  new El({ tag: 'article', role: 'article', class: 'sand-transcript-row', 'aria-label': '' }, '', [
    new El({ tag: 'time', class: 'sand-row-timestamp' }, '4:11 AM', []),
    new El({ role: 'group', 'aria-label': 'Message actions for Suzie message at 4:11:08 AM' }, 'Message actions for Suzie message at 4:11:08 AM', []),
    new El({ class: 'sand-transcript-body' }, 'SuzieYou got a scam WhatsApp from a friend', []),
  ]),
  new El({ role: 'group', 'aria-label': 'Pinned Bots' }, '(empty)', []),
  new El({ role: 'group', 'aria-label': 'Message actions for Suzie message at 8:21:06 PM' }, 'not a turn', []),
]);
const root = new El({}, '', [
  row('You', 'outside the log', 'Message actions for You message at 8:19:00 PM', '8:19 PM'),
  log,
]);
assert.deepStrictEqual(gsm2VoiceTurns(root), []);
const turns = gsm2DesktopTurns(root);
assert.strictEqual(turns[0].speaker, 'caller');
assert.strictEqual(turns[0].text, 'Need a ride. You should hurry.');
assert.strictEqual(turns[0].timestamp, '8:20 PM');
assert.strictEqual(turns[1].speaker, 'assistant');
assert.strictEqual(turns[1].text, 'On the way, and Suzie will text you.');
assert.strictEqual(turns[1].timestamp, '8:21 PM');
assert.strictEqual(turns[2].speaker, 'assistant');
assert.strictEqual(turns[2].text, 'See you soon');
assert.strictEqual(turns[2].timestamp, '8:22 PM');
assert.strictEqual(turns[3].speaker, 'caller');
assert.strictEqual(turns[3].text, 'Still here');
assert.strictEqual(turns[3].timestamp, null);
assert.deepStrictEqual(turns.slice(4, 10).map((turn) => [turn.speaker, turn.text]), [
  ['caller', 'Hello.'],
  ['caller', 'Hi.'],
  ['assistant', 'Hey.'],
  ['caller', "I'm here."],
  ['caller', "Where's"],
  ['assistant', 'Sounds like that got cut off. Go ahead.'],
]);
assert.strictEqual(turns[4].timestamp, '9:05 PM');
assert.deepStrictEqual(turns.slice(10, 14).map((turn) => [turn.speaker, turn.text]), [
  ['caller', 'Hello'],
  ['assistant', 'Hey Safwat.'],
  ['caller', 'Yes, please.'],
  ['assistant', "I'm here. What can I do for you?"],
]);
assert.strictEqual(turns[10].timestamp, '8:18 PM');
assert.strictEqual(turns[14].speaker, 'assistant');
assert.strictEqual(turns[14].text, 'You got a scam WhatsApp from a friend');
assert.strictEqual(turns[14].timestamp, '4:11 AM');
assert.strictEqual(turns.length, 15);
for (const turn of turns) {
  assert.ok(!turn.text.includes('Message actions'));
  assert.ok(!turn.text.includes('Your answer'));
  assert.ok(!turn.text.includes('Voice chat feedback'));
  assert.ok(!turn.text.includes('8:2'));
}
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
                "open_call_tap": {"id": "20261109T120000Z-grokbot", "dir": str(directory), "meta": {"mode": "grokbot"}},
                "last_call_tap": {"id": "20261109T120000Z-grokbot", "dir": str(directory), "meta": {"mode": "grokbot"}},
            }
            st = cew.WatchState(call_id="phone-1", number="+15551212")
            st.tap_id = "20261109T120000Z-grokbot"
            st.tap_dir = str(directory)
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


class SessionMatchTests(unittest.TestCase):
    def test_newest_in_window_ignores_text_chats_and_ties(self) -> None:
        start = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc).timestamp()
        end = start + 180
        sessions = [
            {"href": "/c/notes", "label": "Notes", "started": "2026-11-09T12:00:10Z"},
            {"href": "/c/old", "label": "Voice call", "started": "2026-11-09T10:00:00Z"},
            {"href": "/c/this", "label": "Voice call", "started": "2026-11-09T12:00:30Z"},
            {"href": "/c/newer", "label": "Voice call", "started": "2026-11-09T12:01:00Z"},
        ]
        # The DOM helper already drops unlabeled chats. A session with no parseable
        # start, or one outside the call window, is not this call.
        choice = gt.select_voice_session(sessions[1:], start, end)
        self.assertEqual(choice["status"], "chosen")
        self.assertEqual(choice["href"], "/c/newer")
        self.assertEqual(gt.select_voice_session([], start, end)["status"], "unmatched")
        self.assertEqual(gt.select_voice_session(sessions[1:2], start, end)["status"], "unmatched")
        tied = [
            {"href": "/c/a", "started": "2026-11-09T12:00:30Z"},
            {"href": "/c/b", "started": "2026-11-09T12:00:30Z"},
        ]
        self.assertEqual(gt.select_voice_session(tied, start, end)["status"], "ambiguous")
        self.assertEqual(gt.select_voice_session(tied, None, end)["status"], "unmatched")
        # Slack is only after hangup. A session from before the call, and a
        # voice link with no start time, are not this call.
        early = datetime.fromtimestamp(start - 10, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(
            gt.select_voice_session([{"href": "/c/early", "started": early}], start, end)["status"],
            "unmatched",
        )
        self.assertEqual(
            gt.select_voice_session([{"href": "/c/nostart", "label": "Voice call"}], start, end)["status"],
            "unmatched",
        )
        at = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        chosen = gt.select_voice_session([{"href": "/c/at", "started": at}], start, end)
        self.assertEqual(chosen["status"], "chosen")
        self.assertEqual(chosen["href"], "/c/at")

    def test_unmatched_is_saved_not_ok(self) -> None:
        async def reader(_url: str) -> dict:
            return {"status": "unmatched", "error": "no voice session started during this call"}

        with tempfile.TemporaryDirectory() as tmp:
            pointer = asyncio.run(
                gt.capture_call_transcript(
                    Path(tmp),
                    "call-9",
                    cdp_http="http://127.0.0.1:9333",
                    read_turns=reader,
                )
            )
        self.assertEqual(pointer["status"], "unmatched")
        self.assertEqual(pointer["turns"], 0)

    def test_pending_until_the_file_exists_then_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pointer = gt.transcript_for_alert(
                {"id": "20261109T120000Z-grokbot", "dir": tmp, "meta": {"mode": "grokbot"}}
            )
            self.assertEqual(pointer["status"], "pending")
            self.assertIn("call_transcript", pointer["error"])
            saved = gt.save_transcript_result(
                Path(tmp),
                "20261109T120000Z-grokbot",
                "ok",
                [{"speaker": "assistant", "text": "Here", "timestamp": None}],
            )
        with patch("alert_webhook.post_alert_event") as post:
            gt.announce_transcript(saved)
        payload = post.call_args.args[0]
        self.assertEqual(payload["event"], "call_transcript")
        self.assertEqual(payload["transcript_status"], "ok")
        self.assertEqual(payload["call_id"], "20261109T120000Z-grokbot")

    def test_followup_waits_without_being_the_hub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            pending = gt.transcript_for_alert(
                {"id": "20261109T120000Z-grokbot", "dir": tmp, "meta": {"mode": "grokbot"}}
            )
            posts: list[dict] = []

            def later() -> None:
                time.sleep(0.15)
                gt.save_transcript_result(
                    folder,
                    "20261109T120000Z-grokbot",
                    "ok",
                    [{"speaker": "assistant", "text": "Here", "timestamp": None}],
                )

            threading.Thread(target=later, daemon=True).start()
            t0 = time.monotonic()
            with patch("alert_webhook.post_alert_event", side_effect=posts.append):
                gt.post_transcript_followup(pending, directory=folder, timeout_s=2, poll_s=0.05)
            self.assertGreaterEqual(time.monotonic() - t0, 0.1)
            self.assertEqual(posts[0]["event"], "call_transcript")
            self.assertEqual(posts[0]["transcript_status"], "ok")

        with tempfile.TemporaryDirectory() as tmp:
            missing = gt.transcript_for_alert(
                {"id": "20261109T120100Z-grokbot", "dir": tmp, "meta": {"mode": "grokbot"}}
            )
            posts = []
            with patch("alert_webhook.post_alert_event", side_effect=posts.append):
                gt.post_transcript_followup(missing, directory=Path(tmp), timeout_s=0.05, poll_s=0.02)
            self.assertEqual(posts[0]["transcript_status"], "timeout")


class LevelMergeTests(unittest.TestCase):
    def test_rewrite_keeps_the_saved_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tap = CallTap("20261109T120000Z-grokbot", {"mode": "grokbot"}, record_dir=Path(tmp))
            gt.save_transcript_result(
                tap.dir,
                tap.call_id,
                "ok",
                [{"speaker": "caller", "text": "Hi", "timestamp": None}],
            )
            (tap.dir / "levels.json").write_text(
                json.dumps({"mixes": {"gsm.mp3": {"seconds": 1}}, "transcript": {"status": "empty"}}) + "\n"
            )
            summary = tap.summary()
            summary["mixes"] = {"gsm.mp3": {"seconds": 4}}
            written = tap.write_levels(summary)
            self.assertEqual(written["transcript"]["status"], "ok")
            self.assertEqual(written["mixes"]["gsm.mp3"]["seconds"], 4)
            disk = json.loads((tap.dir / "levels.json").read_text())
            self.assertEqual(disk["transcript"]["status"], "ok")
            self.assertEqual(disk["mixes"]["gsm.mp3"]["seconds"], 4)

    def test_early_close_does_not_drop_levels(self) -> None:
        async def scenario(tap: CallTap) -> dict:
            tap.write_s16("gsm-uplink-8k-mono", b"\x00\x10" * 8, 8000, 1)
            await tap.stop_capture()
            return await tap.close()

        with tempfile.TemporaryDirectory() as tmp:
            tap = CallTap("20261109T120000Z-grokbot", {"mode": "grokbot"}, record_dir=Path(tmp))
            gt.save_transcript_result(
                tap.dir,
                tap.call_id,
                "ok",
                [{"speaker": "caller", "text": "Hi", "timestamp": None}],
            )
            (tap.dir / "levels.json").write_text(
                json.dumps({"mixes": {"gsm.mp3": {"seconds": 1}}, "note": "keep"}) + "\n"
            )
            summary = asyncio.run(scenario(tap))
            self.assertEqual(summary["transcript"]["status"], "ok")
            disk = json.loads((tap.dir / "levels.json").read_text())
            self.assertEqual(disk["mixes"]["gsm.mp3"]["seconds"], 1)
            self.assertEqual(disk["note"], "keep")


class SlowMixTests(unittest.TestCase):
    def test_tap_dir_is_known_while_the_mix_is_still_running(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                previous = root / "old-grokbot"
                previous.mkdir()
                gt.save_transcript_result(
                    previous,
                    "old-grokbot",
                    "ok",
                    [{"speaker": "assistant", "text": "previous", "timestamp": None}],
                )
                tap = CallTap("new-grokbot", {"mode": "grokbot", "id": "new-grokbot"}, record_dir=root)
                identity = {"id": tap.call_id, "dir": str(tap.dir), "meta": dict(tap.meta)}
                started = asyncio.Event()

                async def slow_mix(self: CallTap) -> dict:
                    del self
                    started.set()
                    await asyncio.Event().wait()
                    return {}

                with patch.object(CallTap, "_mix_rooms", slow_mix):
                    close_task = asyncio.create_task(tap.close())
                    await asyncio.wait_for(started.wait(), timeout=1)
                    self.assertFalse(close_task.done())
                    self.assertEqual(identity["id"], "new-grokbot")
                    self.assertTrue(Path(identity["dir"]).is_dir())
                    # The 5s hub cap drops close() while the mix is still going.
                    with self.assertRaises(asyncio.TimeoutError):
                        await asyncio.wait_for(asyncio.shield(close_task), timeout=0.2)
                    self.assertFalse(close_task.done())
                    source = {
                        "id": identity["id"],
                        "dir": identity["dir"],
                        "meta": identity["meta"],
                    }
                    pointer = gt.transcript_for_alert(source)
                    assert pointer is not None
                    self.assertEqual(pointer["status"], "pending")
                    self.assertEqual(pointer["call_id"], "new-grokbot")
                    self.assertNotEqual(pointer.get("call_id"), "old-grokbot")
                    close_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await close_task

        asyncio.run(scenario())


class QueuedDownlinkTests(unittest.TestCase):
    def test_loop_queues_the_wav_write(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tap = CallTap("id", {}, record_dir=Path(tmp))
                calls: list[str] = []
                original = tap.write_s16

                def wrapped(name: str, pcm: bytes, rate: int, channels: int = 1) -> None:
                    calls.append(name)
                    original(name, pcm, rate, channels)

                tap.write_s16 = wrapped  # type: ignore[method-assign]
                tap.write_phone_downlink(b"\x00\x10" * 8, 8000)
                self.assertEqual(calls, [])
                await tap.flush_phone_downlink()
                self.assertEqual(calls, [PHONE_DOWNLINK])
                tap._streams[PHONE_DOWNLINK].close()
                self.assertGreater((tap.dir / f"{PHONE_DOWNLINK}.wav").stat().st_size, 44)

        asyncio.run(scenario())

    def test_flush_joins_the_writer_before_the_wav_closes(self) -> None:
        release = threading.Event()
        order: list[str] = []

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tap = CallTap("id", {}, record_dir=Path(tmp))
                original = tap.write_s16

                def wrapped(name: str, pcm: bytes, rate: int, channels: int = 1) -> None:
                    order.append("write-start")
                    release.wait(timeout=2)
                    original(name, pcm, rate, channels)
                    order.append("write-end")

                tap.write_s16 = wrapped  # type: ignore[method-assign]
                tap.write_phone_downlink(b"\x00\x10" * 8, 8000)
                flush_task = asyncio.create_task(tap.flush_phone_downlink())
                for _ in range(50):
                    if "write-start" in order:
                        break
                    await asyncio.sleep(0.02)
                self.assertIn("write-start", order)
                self.assertFalse(flush_task.done())
                release.set()
                await asyncio.wait_for(flush_task, timeout=2)
                order.append("flush-done")
                tap._streams[PHONE_DOWNLINK].close()
                order.append("closed")

        asyncio.run(scenario())
        self.assertEqual(order, ["write-start", "write-end", "flush-done", "closed"])

    def test_blocked_writer_does_not_keep_the_call_slot(self) -> None:
        import hub

        block = threading.Event()
        started = threading.Event()

        def release_later() -> None:
            time.sleep(2)
            block.set()

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tap = CallTap("id", {}, record_dir=Path(tmp))
                original = tap.write_s16

                def wrapped(name: str, pcm: bytes, rate: int, channels: int = 1) -> None:
                    started.set()
                    block.wait(timeout=5)
                    original(name, pcm, rate, channels)

                tap.write_s16 = wrapped  # type: ignore[method-assign]
                tap.write_phone_downlink(b"\x00\x10" * 8, 8000)
                for _ in range(50):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(started.is_set())
                if hub.live_call.busy:
                    hub.live_call.release()
                self.assertTrue(hub.live_call.claim("/"))
                threading.Thread(target=release_later, daemon=True).start()
                t0 = time.monotonic()
                try:
                    # Flush bound is long, so only the outer wait_for can free the slot.
                    # uncancel() would keep wait_for blocked until the writer returns.
                    with patch.object(call_tap, "DOWNLINK_FLUSH_S", 30):
                        with self.assertRaises(asyncio.TimeoutError):
                            await asyncio.wait_for(tap.stop_capture(), timeout=0.3)
                    elapsed = time.monotonic() - t0
                finally:
                    if hub.live_call.busy:
                        hub.live_call.release()
                self.assertLess(elapsed, 0.8)
                self.assertTrue(hub.live_call.claim("/next"))
                hub.live_call.release()
                block.set()
                if tap._down_task is not None and not tap._down_task.done():
                    await asyncio.wait_for(tap._down_task, timeout=3)

        asyncio.run(scenario())


class ServedBundleTests(unittest.TestCase):
    def test_entry_bundle_has_the_transcript_label(self) -> None:
        dist = HERE / "portal" / "dist"
        html = (dist / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r'src="([^"]+\.js)"', html)
        self.assertTrue(scripts)
        self.assertFalse(any("calls-" in src for src in scripts))
        blob = ""
        for src in scripts:
            rel = src.split("/portal/", 1)[-1] if "/portal/" in src else src.lstrip("/")
            path = dist / rel
            self.assertTrue(path.is_file(), src)
            blob += path.read_text(encoding="utf-8")
        self.assertIn("meta transcript", blob)
        self.assertIn("transcript ${", blob)
        self.assertIn("SMS failed", blob)
        self.assertIn("SMS timed out", blob)
        # calls-*.js is the offline mock list, not the calls page. The hub serves
        # the script named by dist/index.html, and that bundle has the label.
        mock = list((dist / "assets").glob("calls-*.js"))
        self.assertTrue(mock)
        self.assertNotIn("meta transcript", mock[0].read_text(encoding="utf-8"))

    def test_inflight_click_checks_the_page_token(self) -> None:
        script = r"""
const assert = require('assert');
const clicks = [];
global.window = global;
global.document = {
  querySelectorAll() {
    return [{
      getAttribute(name) { return name === 'href' ? '/c/voice' : null; },
      click() { clicks.push('clicked'); },
    }];
  },
};
const click = %s;
window.__gsm2TranscriptToken = null;
eval(click);
assert.deepStrictEqual(clicks, []);
window.__gsm2TranscriptToken = 'tok';
eval(click);
assert.deepStrictEqual(clicks, ['clicked']);
window.__gsm2TranscriptToken = 'other';
eval(click);
assert.deepStrictEqual(clicks, ['clicked']);
""" % json.dumps(gt._click_js("/c/voice", "tok"))
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

    def test_click_is_sent_after_the_python_wait(self) -> None:
        calls: list[str] = []

        async def fake_eval(ws: object, nid: list[int], expression: str) -> bool:
            del ws, nid
            calls.append(expression)
            return True

        async def scenario() -> None:
            abort = asyncio.Event()
            with patch.object(gt, "CLICK_GUARD_MS", 40), patch.object(gt, "_cdp_eval", fake_eval):
                await gt._wait_then_click(None, [0], "/c/voice", "tok", abort)
            self.assertEqual(len(calls), 1)
            self.assertIn("__gsm2TranscriptToken", calls[0])
            self.assertNotIn("setTimeout", calls[0])
            calls.clear()
            abort.set()
            with patch.object(gt, "CLICK_GUARD_MS", 200), patch.object(gt, "_cdp_eval", fake_eval):
                with self.assertRaises(asyncio.CancelledError):
                    await gt._wait_then_click(None, [0], "/c/voice", "tok", abort)
            self.assertEqual(calls, [])

        asyncio.run(scenario())


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

    def test_new_call_aborts_the_reader_before_switch(self) -> None:
        from test_grokbot_mode import FakePatchbay, voice

        pb = FakePatchbay()
        v = voice(pb)
        order: list[str] = []

        async def scenario() -> None:
            await v.start()
            started = asyncio.Event()

            async def hook(_res: dict) -> None:
                started.set()
                try:
                    while not v.transcript_abort.is_set():
                        await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    order.append("cancelled")
                    return
                if v.transcript_abort.is_set():
                    order.append("aborted")
                    return
                order.append("click")

            v.transcript_abort_s = 0.5
            task = v.stop_later(on_stopped=hook)
            await asyncio.wait_for(started.wait(), timeout=2)
            t0 = time.monotonic()
            await v.start()
            self.assertLess(time.monotonic() - t0, 1.5)
            await asyncio.wait_for(task, timeout=1)
            self.assertNotIn("click", order)
            self.assertTrue(set(order) & {"aborted", "cancelled"})
            self.assertEqual(pb.calls()[-1], "switch suzie --json")

        asyncio.run(scenario())

    def test_stubborn_reader_does_not_hold_the_next_switch(self) -> None:
        from test_grokbot_mode import FakePatchbay, voice

        pb = FakePatchbay()
        v = voice(pb)
        order: list[str] = []

        async def scenario() -> None:
            await v.start()
            started = asyncio.Event()

            async def hook(_res: dict) -> None:
                started.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    try:
                        await asyncio.sleep(30)
                    except asyncio.CancelledError:
                        return
                order.append("click")

            v.transcript_abort_s = 0.3
            v.stop_later(on_stopped=hook)
            await asyncio.wait_for(started.wait(), timeout=2)
            t0 = time.monotonic()
            await v.start()
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertNotIn("click", order)
            self.assertEqual(pb.calls()[-1], "switch suzie --json")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
