"""Save a Grok Bot voice-session transcript after the call has ended.

The live session is never clicked. ``patchbay off`` runs first; only then does
this module open the voice session that started during this call and read both
sides. A failure is written as ``transcript.json`` with ``status`` and ``error``
and is not raised to the call or the alert. A new call aborts the read.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

LOG = logging.getLogger("gsm2computer-hub.grokbot")

TRANSCRIPT_NAME = "transcript.json"
READ_TIMEOUT_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_TRANSCRIPT_TIMEOUT_S", "12"))
# A session stamped slightly after hangup can still be this call. Slack does
# not extend before the call, and a link with no start time is not a match.
CALL_WINDOW_SLACK_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_TRANSCRIPT_SLACK_S", "30"))
# How long the call_ended process waits for transcript.json before posting
# call_transcript. The read itself is capped near 12s, after patchbay off.
FOLLOWUP_WAIT_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_TRANSCRIPT_FOLLOWUP_S", "25"))
# The click expression is already on the wire before abort can run. It only
# clicks if this page token is still the one the reader armed.
CLICK_GUARD_MS = 150
_KEPT_STATUSES = {
    "unmatched",
    "ambiguous",
    "aborted",
    "no_cdp",
    "error",
    "off_failed",
    "empty",
    "pending",
    "not_found",
    "unscoped",
}

# Runs in the Grok page. ``root`` defaults to document so tests can pass a fake tree.
VOICE_DOM_JS = r"""
function gsm2Text(node) {
  const clone = node.cloneNode(true);
  if (clone.querySelectorAll) {
    clone.querySelectorAll(".sr-only, .thinking-container, time").forEach((child) => child.remove());
  }
  return String(clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim();
}
function gsm2Stamp(node) {
  const own = node.getAttribute && (node.getAttribute("data-timestamp") || node.getAttribute("data-time"));
  if (own) return own;
  const time = node.querySelector && node.querySelector("time");
  if (!time) return null;
  return time.getAttribute("datetime") || String(time.textContent || "").trim() || null;
}
function gsm2Speaker(node) {
  const testid = String((node.getAttribute && node.getAttribute("data-testid")) || "").toLowerCase();
  if (testid === "user-message") return "caller";
  if (testid === "assistant-message") return "assistant";
  const role = String(
    (node.getAttribute && (node.getAttribute("data-role") || node.getAttribute("data-author") || node.getAttribute("data-speaker"))) || ""
  ).toLowerCase();
  if (role === "user" || role === "caller" || role === "human") return "caller";
  if (role === "assistant" || role === "grok" || role === "suzie") return "assistant";
  return null;
}
function gsm2VoiceTurns(root) {
  const doc = root || document;
  const nodes = Array.from(doc.querySelectorAll(
    '[data-testid="user-message"], [data-testid="assistant-message"], div.message-bubble'
  ));
  const outer = nodes.filter((node) => !nodes.some((other) => other !== node && node.contains && node.contains(other)));
  const turns = [];
  for (const node of outer) {
    const speaker = gsm2Speaker(node);
    if (!speaker) continue;
    const text = gsm2Text(node);
    if (!text) continue;
    turns.push({ speaker: speaker, text: text, timestamp: gsm2Stamp(node) });
  }
  return turns;
}
function gsm2Visible(el) {
  if (!el || !el.getBoundingClientRect) return true;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function gsm2SessionStarted(el) {
  const own = el.getAttribute && (el.getAttribute("data-started") || el.getAttribute("data-timestamp") || el.getAttribute("data-time"));
  if (own) return own;
  const time = el.querySelector && el.querySelector("time");
  if (!time) return null;
  return time.getAttribute("datetime") || null;
}
function gsm2VoiceSessions(root) {
  const doc = root || document;
  const links = Array.from(doc.querySelectorAll('a[href^="/c/"]')).filter(gsm2Visible);
  const voice = /voice|call|phone/i;
  const start = /^(start|new|begin)\b|start voice|new voice/i;
  const sessions = [];
  for (const el of links) {
    const href = String((el.getAttribute && el.getAttribute("href")) || "");
    if (!href.startsWith("/c/")) continue;
    const label = String(el.innerText || "") + " " + String((el.getAttribute && el.getAttribute("aria-label")) || "");
    if (!voice.test(label) || start.test(label)) continue;
    sessions.push({ href: href, label: label.trim(), started: gsm2SessionStarted(el) });
  }
  return sessions;
}
function gsm2DesktopChrome(el) {
  if (!el || !el.getAttribute) return false;
  const label = String(el.getAttribute("aria-label") || "");
  if (/^Message actions\b/i.test(label)) return true;
  if (label === "Your answer" || label === "Voice chat feedback") return true;
  if (el.getAttribute("data-voice-call-summary") !== null) return true;
  if (el.getAttribute("data-voice-call-duration") !== null) return true;
  const cls = String(el.getAttribute("class") || "");
  if (/(?:^|\s)sand-row-timestamp(?:\s|$)/.test(cls)) return true;
  const tag = String(el.tagName || el.localName || "").toLowerCase();
  if (tag === "time" || tag === "button") return true;
  return false;
}
function gsm2DesktopInChrome(el) {
  let cur = el;
  while (cur) {
    if (gsm2DesktopChrome(cur)) return true;
    cur = cur.parentElement || cur.parent || null;
  }
  return false;
}
function gsm2DesktopActionsName(row) {
  const nodes = row.querySelectorAll ? Array.from(row.querySelectorAll("[aria-label]")) : [];
  for (const node of nodes) {
    const found = /^Message actions for (.+) message at\b/i.exec(String(node.getAttribute("aria-label") || ""));
    if (found) return found[1].replace(/\s+/g, " ").trim();
  }
  return "";
}
function gsm2DesktopRole(name) {
  const text = String(name || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (text === "you" || text === "your") return "caller";
  if (text) return "assistant";
  return null;
}
function gsm2DesktopOwnText(el) {
  if (el.childNodes) {
    return Array.from(el.childNodes)
      .filter((node) => node.nodeType === 3)
      .map((node) => node.textContent || "")
      .join(" ");
  }
  return el.text || "";
}
function gsm2DesktopPlain(el) {
  if (!el || gsm2DesktopChrome(el)) return "";
  const parts = [];
  const own = String(gsm2DesktopOwnText(el) || "").replace(/\s+/g, " ").trim();
  if (own) parts.push(own);
  const kids = el.children ? Array.from(el.children) : [];
  for (const child of kids) {
    const text = gsm2DesktopPlain(child);
    if (text) parts.push(text);
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}
function gsm2DesktopParts(el) {
  if (!el) return [];
  if (el.childNodes && el.childNodes.length) {
    const parts = [];
    for (const node of Array.from(el.childNodes)) {
      if (node.nodeType === 3) parts.push({ kind: "text", text: node.textContent || "" });
      else if (node.nodeType === 1) parts.push({ kind: "element", el: node });
    }
    return parts;
  }
  const parts = [];
  if (el.text) parts.push({ kind: "text", text: el.text });
  const kids = el.children ? Array.from(el.children) : [];
  for (const child of kids) parts.push({ kind: "element", el: child });
  return parts;
}
function gsm2DesktopKnownNames(root) {
  const names = new Set(["you", "your"]);
  const nodes = root && root.querySelectorAll ? Array.from(root.querySelectorAll("[aria-label], *")) : [];
  for (const node of nodes) {
    const found = /^Message actions for (.+) message at\b/i.exec(String(node.getAttribute && node.getAttribute("aria-label") || ""));
    if (found) names.add(found[1].replace(/\s+/g, " ").trim().toLowerCase());
    const cls = String(node.getAttribute && node.getAttribute("class") || "");
    if (/(?:^|\s)sand-chat-header__identity(?:\s|$)/.test(cls)) {
      const text = gsm2DesktopPlain(node).replace(/\s+/g, " ").trim().toLowerCase();
      if (text) names.add(text);
    }
  }
  return names;
}
function gsm2DesktopHasAttr(el, name) {
  return !!(el && el.getAttribute && el.getAttribute(name) !== null);
}
function gsm2DesktopInsideVoice(el) {
  let cur = el;
  while (cur) {
    if (gsm2DesktopHasAttr(cur, "data-voice-call-transcript")) return true;
    cur = cur.parentElement || cur.parent || null;
  }
  return false;
}
function gsm2DesktopExplicitRole(el) {
  if (!el || !el.getAttribute || gsm2DesktopChrome(el)) return null;
  const raw = String(
    el.getAttribute("data-role") || el.getAttribute("data-speaker") || el.getAttribute("data-author") || el.getAttribute("data-from") || ""
  ).replace(/\s+/g, " ").trim().toLowerCase();
  if (raw === "user" || raw === "human" || raw === "caller" || raw === "you") return "caller";
  if (raw === "assistant" || raw === "agent" || raw === "bot" || raw === "model" || raw === "grok") return "assistant";
  const cls = String(el.getAttribute("class") || "");
  if (/(?:^|\s)sand-fill-bubble-user(?:\s|$)/.test(cls)) return "caller";
  if (/(?:^|\s)sand-fill-bubble-agent(?:\s|$)/.test(cls)) return "assistant";
  return null;
}
function gsm2DesktopIsSpeakerMarker(el, names) {
  if (!el || !el.getAttribute || gsm2DesktopChrome(el) || gsm2DesktopInChrome(el)) return false;
  const text = gsm2DesktopPlain(el).replace(/\s+/g, " ").trim().toLowerCase();
  return Boolean(text) && names.has(text);
}
function gsm2DesktopStripGlued(text, name) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  const raw = String(name || "").trim();
  if (!raw || value.length < raw.length) return value;
  if (value.slice(0, raw.length).toLowerCase() !== raw.toLowerCase()) return value;
  const rest = value.slice(raw.length);
  if (/^[A-Z]/.test(rest)) return rest.trim();
  if (/^\s/.test(rest)) {
    const after = rest.replace(/^\s+/, "");
    if (/^you$/i.test(raw) && /^[a-z]/.test(after)) return value;
    return after.trim();
  }
  return value;
}
function gsm2DesktopCollectText(el, names) {
  const parts = [];
  function visit(node) {
    if (!node || gsm2DesktopChrome(node)) return;
    if (node !== el && gsm2DesktopIsSpeakerMarker(node, names)) return;
    const bits = gsm2DesktopParts(node);
    const hasElement = bits.some((bit) => bit.kind === "element");
    for (const bit of bits) {
      if (bit.kind === "text") {
        const raw = String(bit.text || "").replace(/\s+/g, " ").trim();
        if (!raw) continue;
        if (names.has(raw.toLowerCase()) && (hasElement || bits.length > 1)) continue;
        parts.push(raw);
      } else {
        visit(bit.el);
      }
    }
  }
  visit(el);
  return parts.join(" ").replace(/\s+/g, " ").trim();
}
function gsm2DesktopExplicitUtterances(row) {
  if (!row.querySelectorAll) return null;
  const signaled = Array.from(row.querySelectorAll("*")).filter((el) => gsm2DesktopExplicitRole(el));
  const inner = signaled.filter((el) => !signaled.some((other) => other !== el && el.contains && el.contains(other)));
  if (!inner.length) return null;
  const parent = inner[0].parentElement || inner[0].parent;
  const sameParent = parent && inner.every((el) => (el.parentElement || el.parent) === parent);
  if (sameParent) {
    const kids = parent.children ? Array.from(parent.children) : [];
    const voice = gsm2DesktopInsideVoice(parent);
    if (voice) {
      const cards = kids.filter((el) => gsm2DesktopExplicitRole(el));
      return cards.length ? cards.map((el) => ({ el: el, role: gsm2DesktopExplicitRole(el) })) : null;
    }
    const bubbles = kids.filter((el) => !gsm2DesktopChrome(el) && !gsm2DesktopInChrome(el) && gsm2DesktopPlain(el));
    if (bubbles.length >= 2 && bubbles.some((el) => gsm2DesktopExplicitRole(el) === "caller") && bubbles.some((el) => inner.includes(el))) {
      return bubbles.map((el) => ({ el: el, role: gsm2DesktopExplicitRole(el) || "assistant" }));
    }
  }
  if (inner.length >= 2) return inner.map((el) => ({ el: el, role: gsm2DesktopExplicitRole(el) }));
  return null;
}
function gsm2DesktopSegments(row, names, fallbackName) {
  const segments = [];
  let current = null;
  function start(role, name) {
    current = { speaker: role, name: name, parts: [] };
    segments.push(current);
  }
  function append(text) {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    if (!value) return;
    if (!current) {
      const role = gsm2DesktopRole(fallbackName);
      if (!role) return;
      start(role, fallbackName);
    }
    current.parts.push(value);
  }
  function visit(el) {
    if (!el || gsm2DesktopChrome(el)) return;
    if (gsm2DesktopIsSpeakerMarker(el, names)) {
      const name = gsm2DesktopPlain(el);
      const role = gsm2DesktopRole(name);
      if (role) start(role, name);
      return;
    }
    const bits = gsm2DesktopParts(el);
    const hasElement = bits.some((bit) => bit.kind === "element");
    for (const bit of bits) {
      if (bit.kind === "text") {
        const raw = String(bit.text || "").replace(/\s+/g, " ").trim();
        if (!raw) continue;
        if (names.has(raw.toLowerCase()) && (hasElement || bits.length > 1)) {
          const role = gsm2DesktopRole(raw);
          if (role) start(role, raw);
          continue;
        }
        append(raw);
      } else {
        visit(bit.el);
      }
    }
  }
  visit(row);
  const turns = [];
  for (const segment of segments) {
    if (!segment.speaker) continue;
    const text = gsm2DesktopStripGlued(segment.parts.join(" "), segment.name);
    if (!text) continue;
    turns.push({ speaker: segment.speaker, text: text });
  }
  return turns;
}
function gsm2DesktopStamp(row) {
  const timed = row.querySelector && row.querySelector(".sand-row-timestamp");
  if (timed) {
    const text = String(timed.textContent || timed.innerText || "").replace(/\s+/g, " ").trim();
    if (text) return text;
  }
  const stamp = gsm2Stamp(row);
  if (stamp) return stamp;
  const nodes = row.querySelectorAll ? Array.from(row.querySelectorAll("[aria-label]")) : [];
  for (const node of nodes) {
    const found = /^Message actions for .+ message at (.+)$/i.exec(String(node.getAttribute("aria-label") || ""));
    if (found) return found[1].trim();
  }
  return null;
}
function gsm2DesktopRowTurns(row, names) {
  const stamp = gsm2DesktopStamp(row);
  const explicit = gsm2DesktopExplicitUtterances(row);
  const turns = [];
  if (explicit) {
    const botName = Array.from(names).find((name) => name !== "you" && name !== "your") || "";
    for (const item of explicit) {
      const label = item.role === "caller" ? "You" : botName;
      const text = gsm2DesktopStripGlued(gsm2DesktopCollectText(item.el, names), label);
      if (!item.role || !text) continue;
      turns.push({ speaker: item.role, text: text, timestamp: stamp });
    }
    return turns;
  }
  for (const segment of gsm2DesktopSegments(row, names, gsm2DesktopActionsName(row))) {
    turns.push({ speaker: segment.speaker, text: segment.text, timestamp: stamp });
  }
  return turns;
}
function gsm2DesktopRows(root) {
  const doc = root || document;
  const log = doc.querySelector && doc.querySelector('[role="log"][aria-label="Conversation transcript"]');
  if (!log || !log.querySelectorAll) return [];
  return Array.from(log.querySelectorAll(
    'article.sand-transcript-row, article.sand-virtual-transcript__row, [role="article"].sand-transcript-row, [role="article"].sand-virtual-transcript__row'
  ));
}
function gsm2DesktopTurns(root) {
  const doc = root || document;
  const names = gsm2DesktopKnownNames(doc);
  const turns = [];
  const seen = new Set();
  for (const row of gsm2DesktopRows(doc)) {
    if (seen.has(row)) continue;
    seen.add(row);
    for (const turn of gsm2DesktopRowTurns(row, names)) turns.push(turn);
  }
  return turns;
}
function gsm2DesktopVoiceCapture(root) {
  const doc = root || document;
  const names = gsm2DesktopKnownNames(doc);
  const voiceRows = [];
  const seen = new Set();
  for (const row of gsm2DesktopRows(doc)) {
    if (seen.has(row)) continue;
    seen.add(row);
    const key = String((row.getAttribute && row.getAttribute("data-row-key")) || "");
    if (!/^voice-call-call-/.test(key)) continue;
    const durationNode = row.querySelector && row.querySelector("[data-voice-call-duration]");
    const duration = durationNode
      ? String(durationNode.textContent || durationNode.innerText || "").replace(/\s+/g, " ").trim()
      : "";
    const time = row.querySelector && row.querySelector("time");
    const absolute = (time && time.getAttribute && time.getAttribute("datetime"))
      || (row.getAttribute && (row.getAttribute("data-timestamp") || row.getAttribute("data-time") || row.getAttribute("data-started")))
      || "";
    voiceRows.push({
      key: key,
      timestamp: gsm2DesktopStamp(row),
      absolute: absolute || null,
      duration: duration || null,
      turns: gsm2DesktopRowTurns(row, names),
    });
  }
  return { voiceRows: voiceRows };
}
"""

_READ_EXPR = VOICE_DOM_JS + "\ngsm2VoiceTurns(document)"
_DESKTOP_READ_EXPR = VOICE_DOM_JS + "\ngsm2DesktopVoiceCapture(document)"
_SESSIONS_EXPR = VOICE_DOM_JS + "\ngsm2VoiceSessions(document)"
# Grok Bot desktop shell. Not a grok.com host. The chat DOM is different.
_DESKTOP_RENDERER_SUFFIX = "/resources/app.asar/dist/renderer/index.html"


def _is_desktop_renderer_url(url: str) -> bool:
    """True only for the Electron renderer page, not any other file:// URL."""
    parts = urlsplit(url or "")
    if parts.scheme != "file":
        return False
    path = unquote(parts.path or "").replace("\\", "/")
    if path and not path.startswith("/"):
        path = "/" + path
    return path.endswith(_DESKTOP_RENDERER_SUFFIX)


def _is_grok_url(url: str) -> bool:
    """True for the Grok origin itself, not a host that merely contains the name."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if host == "grok.com" or host.endswith(".grok.com"):
        return True
    if host == "grok.x.ai" or host.endswith(".grok.x.ai"):
        return True
    if host in {"x.com", "www.x.com"} and (parts.path == "/i/grok" or parts.path.startswith("/i/grok/")):
        return True
    return False


def _loopback_ws(url: str) -> bool:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    return parts.scheme in {"ws", "wss"} and host in {"127.0.0.1", "localhost", "::1"}


def _parse_started(value: Any) -> Optional[float]:
    """Wall-clock epoch seconds. Media offsets and unlabeled text are not starts."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:
            return number / 1000.0
        if number > 1e9:
            return number
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        return _parse_started(number)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def select_voice_session(
    sessions: Any,
    started_at: Optional[float],
    ended_at: Optional[float],
) -> dict[str, Any]:
    """Pick the newest voice session that started during this call.

    No unlabeled ``/c/`` fallback. A link with no parseable start is ignored,
    including one that would otherwise fall in the pre-call slack. Slack applies
    only after hangup. Zero matches are ``unmatched``. Two sessions that share
    the newest in-window start are ``ambiguous``.
    """
    if started_at is None:
        return {"status": "unmatched", "error": "call start time is unknown"}
    end = time_now() if ended_at is None else float(ended_at)
    window_start = float(started_at)
    window_end = end + CALL_WINDOW_SLACK_S
    rows = sessions if isinstance(sessions, list) else []
    chosen: list[tuple[float, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "")
        if not href.startswith("/c/"):
            continue
        started = _parse_started(row.get("started"))
        if started is None or started < window_start or started > window_end:
            continue
        chosen.append((started, href))
    if not chosen:
        return {"status": "unmatched", "error": "no voice session started during this call"}
    chosen.sort()
    if len(chosen) >= 2 and chosen[-1][0] == chosen[-2][0]:
        return {
            "status": "ambiguous",
            "error": f"{len(chosen)} voice sessions share the newest start in the call window",
        }
    started, href = chosen[-1]
    return {"status": "chosen", "href": href, "started": started}


def time_now() -> float:
    return datetime.now(timezone.utc).timestamp()


_VOICE_ROW_KEY = re.compile(r"^voice-call-call-(?P<id>.+?)(?:@\d+)?$")
_DURATION_HMS = re.compile(r"^(?:(\d+):)?(\d+):(\d+)$")
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b", re.I)


def _parse_duration_s(value: Any) -> Optional[float]:
    """Seconds from a voice-card duration. Clock-only stamps are not durations."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    clock = _DURATION_HMS.match(text)
    if clock:
        hours = int(clock.group(1) or 0)
        minutes = int(clock.group(2))
        seconds = int(clock.group(3))
        return float(hours * 3600 + minutes * 60 + seconds)
    total = 0.0
    found = False
    for match in _DURATION_PART.finditer(text):
        found = True
        amount = float(match.group(1))
        unit = match.group(2)
        if unit.startswith("h"):
            total += amount * 3600
        elif unit.startswith("m"):
            total += amount * 60
        else:
            total += amount
    return total if found else None


def _voice_row_time(row: dict[str, Any]) -> Optional[float]:
    """Absolute start of a voice-call row. A clock label such as ``8:18 PM`` is not one."""
    key = str(row.get("key") or "")
    matched = _VOICE_ROW_KEY.match(key)
    candidates = [row.get("absolute"), row.get("timestamp")]
    if matched:
        candidates.append(matched.group("id"))
    for candidate in candidates:
        parsed = _parse_started(candidate)
        if parsed is not None:
            return parsed
    return None


def scope_desktop_voice(
    raw: Any,
    started_at: Optional[float],
    ended_at: Optional[float],
) -> dict[str, Any]:
    """Keep the voice-call card for this call. Older mounted history is not saved.

    A row counts only when ``data-row-key`` is ``voice-call-call-<id>`` and its
    absolute time (``time[datetime]``, a data timestamp, or an epoch in the
    key) falls in ``[start - slack, end + slack]``. ``data-voice-call-duration``
    is recorded on the row. A clock stamp such as ``8:18 PM`` does not choose a
    day, so a newest row that has only that stamp is ``unscoped`` and its turns
    are dropped. No voice-call row is ``not_found``. A card in the window with
    no utterances is ``empty``.
    """
    payload = raw
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict) or "voiceRows" not in payload:
        return {
            "status": "unscoped",
            "error": "desktop transcript was not a voice-call row list",
            "turns": [],
        }
    rows = [row for row in payload.get("voiceRows") or [] if isinstance(row, dict)]
    rows = [row for row in rows if _VOICE_ROW_KEY.match(str(row.get("key") or ""))]
    if not rows:
        return {
            "status": "not_found",
            "error": "no voice-call row in the open transcript",
            "turns": [],
        }
    if started_at is None:
        return {
            "status": "unscoped",
            "error": "call start time is unknown; voice-call rows were not saved",
            "turns": [],
        }
    end = time_now() if ended_at is None else float(ended_at)
    window_start = float(started_at) - CALL_WINDOW_SLACK_S
    window_end = end + CALL_WINDOW_SLACK_S
    placed: list[tuple[float, dict[str, Any]]] = []
    unplaced: list[dict[str, Any]] = []
    for row in rows:
        when = _voice_row_time(row)
        if when is None:
            unplaced.append(row)
        else:
            placed.append((when, row))
    matched = [(when, row) for when, row in placed if window_start <= when <= window_end]
    if not matched and unplaced:
        newest = unplaced[-1]
        label = str(newest.get("timestamp") or "").strip() or "no date"
        duration = str(newest.get("duration") or "").strip()
        parsed = _parse_duration_s(duration)
        detail = f"newest voice-call row {newest.get('key')} is stamped {label}"
        if duration:
            detail += f" duration {duration}"
            if parsed is not None:
                detail += f" ({int(parsed)}s)"
        detail += "; clock time is not tied to this call"
        return {"status": "unscoped", "error": detail, "turns": []}
    if not matched:
        return {
            "status": "not_found",
            "error": "no voice-call row falls in this call's time window",
            "turns": [],
        }
    matched.sort(key=lambda item: item[0])
    if len(matched) >= 2 and matched[-1][0] == matched[-2][0]:
        return {
            "status": "unscoped",
            "error": "two voice-call rows share the newest time in this call",
            "turns": [],
        }
    _when, row = matched[-1]
    turns = [turn for turn in (row.get("turns") or []) if isinstance(turn, dict)]
    if not turns:
        return {"status": "empty", "error": None, "turns": []}
    return {"status": "ok", "turns": turns}


def _loopback_http(url: str) -> Optional[str]:
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        raw = f"http://127.0.0.1:{raw}"
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    if parts.port is None:
        return None
    path = parts.path or ""
    if path in {"", "/"}:
        path = ""
    return f"{parts.scheme}://{host}:{parts.port}{path}".rstrip("/")


def cdp_base_from_status(status: Optional[dict[str, Any]]) -> Optional[str]:
    """Loopback CDP HTTP base from the environment or ``patchbay status --json``."""
    env = _loopback_http(os.environ.get("GSM2COMPUTER_GROKBOT_CDP", ""))
    if env:
        return env
    blob = status or {}
    nested = blob.get("json")
    if isinstance(nested, dict):
        blob = nested
    if not isinstance(blob, dict):
        return None
    for key in ("cdp_http", "cdp_url", "devtools_http"):
        found = _loopback_http(str(blob.get(key) or ""))
        if found:
            return found
    port = blob.get("cdp_port")
    if port is None and isinstance(blob.get("cdp"), dict):
        port = blob["cdp"].get("port")
    if port is not None:
        return _loopback_http(str(port))
    return None


def _speaker(raw: Any) -> Optional[str]:
    """Caller or assistant. Anything else is dropped, not stored as a turn."""
    text = str(raw or "").strip().lower()
    if text in {"caller", "user", "human", "you"}:
        return "caller"
    if text in {"assistant", "grok", "suzie", "bot", "model"}:
        return "assistant"
    return None


def _timestamp(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return raw
    text = str(raw).strip()
    return text or None


def normalize_turns(raw: Any, call_id: str) -> dict[str, Any]:
    """Normalize CDP nodes or already-shaped turns. Empty input is status ``empty``.

    A reader result of ``unmatched`` or ``ambiguous`` stays that status. Turns
    whose speaker is not caller or assistant are dropped. ``not_found`` and
    ``unscoped`` keep no turns, so an older mounted chat cannot be saved.
    """
    if isinstance(raw, dict) and str(raw.get("status") or "") in {"not_found", "unscoped"}:
        return {
            "call_id": call_id,
            "status": str(raw.get("status")),
            "error": raw.get("error"),
            "turns": [],
        }
    if isinstance(raw, dict) and raw.get("status") in _KEPT_STATUSES and "turns" not in raw:
        return {
            "call_id": call_id,
            "status": str(raw.get("status")),
            "error": raw.get("error"),
            "turns": [],
        }
    rows: list[Any]
    if isinstance(raw, dict):
        if raw.get("status") in _KEPT_STATUSES and not raw.get("turns"):
            return {
                "call_id": call_id,
                "status": str(raw.get("status")),
                "error": raw.get("error"),
                "turns": [],
            }
        rows = list(raw.get("turns") or [])
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    turns: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        speaker = row.get("speaker")
        if not speaker:
            testid = str(row.get("testid") or "").lower()
            role = str(row.get("role") or "").lower()
            if testid == "user-message" or role in {"user", "caller", "human"}:
                speaker = "caller"
            elif testid == "assistant-message" or role in {"assistant", "grok", "suzie"}:
                speaker = "assistant"
        mapped = _speaker(speaker)
        if mapped is None:
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        turns.append(
            {
                "speaker": mapped,
                "text": text,
                "timestamp": _timestamp(row.get("timestamp")),
            }
        )
    return {
        "call_id": call_id,
        "status": "ok" if turns else "empty",
        "error": None,
        "turns": turns,
    }


def pointer_from_document(document: dict[str, Any], path: Path) -> dict[str, Any]:
    turns = list(document.get("turns") or [])
    preview = []
    for turn in turns[:6]:
        if not isinstance(turn, dict):
            continue
        preview.append(
            {
                "speaker": turn.get("speaker"),
                "text": str(turn.get("text") or "")[:160],
                "timestamp": turn.get("timestamp"),
            }
        )
    return {
        "status": document.get("status") or "error",
        "file": TRANSCRIPT_NAME,
        "path": str(path),
        "turns": len(turns),
        "error": document.get("error"),
        "call_id": document.get("call_id"),
        "preview": preview,
    }


def save_transcript_result(
    directory: Path,
    call_id: str,
    status: str,
    turns: Optional[list[dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "call_id": call_id,
        "status": status,
        "error": (str(error)[:500] if error else None),
        "turns": turns or [],
    }
    path = directory / TRANSCRIPT_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    pointer = pointer_from_document(document, path)
    if status == "ok":
        LOG.info("grok transcript saved call_id=%s turns=%s", call_id, pointer["turns"])
    else:
        LOG.warning("grok transcript %s call_id=%s error=%s", status, call_id, document["error"])
    return pointer


def load_saved_pointer(directory: Path) -> Optional[dict[str, Any]]:
    path = directory / TRANSCRIPT_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "unreadable",
            "file": TRANSCRIPT_NAME,
            "path": str(path),
            "turns": 0,
            "error": str(exc),
            "call_id": None,
            "preview": [],
        }
    if not isinstance(data, dict):
        return {
            "status": "unreadable",
            "file": TRANSCRIPT_NAME,
            "path": str(path),
            "turns": 0,
            "error": "transcript.json is not an object",
            "call_id": None,
            "preview": [],
        }
    return pointer_from_document(data, path)


def attach_levels(directory: Path, pointer: dict[str, Any]) -> None:
    """Merge the pointer into levels.json. Other keys already in the file stay."""
    levels = directory / "levels.json"
    data: dict[str, Any] = {}
    if levels.is_file():
        try:
            parsed = json.loads(levels.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(parsed, dict):
            return
        data = parsed
    data["transcript"] = pointer
    tmp = levels.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(levels)


def _safe_folder(record_dir: Path, name: str) -> Optional[Path]:
    label = (name or "").strip()
    if not label or label in {".", ".."} or "/" in label or "\\" in label:
        return None
    folder = record_dir / label
    try:
        folder.resolve().relative_to(record_dir.resolve())
    except (OSError, ValueError):
        return None
    return folder


def pointer_for_saved_call(call: dict[str, Any], record_dir: Path) -> Optional[dict[str, Any]]:
    """Pointer for a portal call row, from the recording directory. None when no file exists."""
    names: list[str] = []
    session_id = str(call.get("session_id") or "").strip()
    if session_id:
        names.append(session_id)
    raw = call.get("tap_summary")
    tap: Optional[dict[str, Any]] = None
    if isinstance(raw, dict):
        tap = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            tap = parsed
    if tap:
        tap_id = str(tap.get("id") or "").strip()
        if tap_id:
            names.append(tap_id)
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        folder = _safe_folder(Path(record_dir), name)
        if folder is None:
            continue
        found = load_saved_pointer(folder)
        if found:
            return found
    # A raw directory is only a fallback. session_id and the tap id win so a
    # stale last_call_tap cannot label this call with another call's transcript.
    if tap:
        directory = str(tap.get("dir") or "").strip()
        if directory:
            folder = Path(directory)
            try:
                folder.resolve().relative_to(Path(record_dir).resolve())
            except (OSError, ValueError):
                folder = None
            if folder is not None:
                return load_saved_pointer(folder)
    return None


def _http_json(url: str, timeout: float) -> Any:
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _grok_page(targets: Any) -> Optional[dict[str, Any]]:
    if not isinstance(targets, list):
        return None
    pages = [t for t in targets if isinstance(t, dict) and t.get("type") == "page"]
    for page in pages:
        page_url = str(page.get("url") or "")
        if not (_is_grok_url(page_url) or _is_desktop_renderer_url(page_url)):
            continue
        ws_url = str(page.get("webSocketDebuggerUrl") or "")
        if not _loopback_ws(ws_url):
            continue
        return page
    return None


def _expr_value(message: dict[str, Any]) -> Any:
    result = message.get("result") or {}
    if not isinstance(result, dict):
        return None
    inner = result.get("result") or {}
    if isinstance(inner, dict) and "value" in inner:
        return inner.get("value")
    return None


async def _cdp_eval(ws: Any, nid: list[int], expression: str) -> Any:
    nid[0] += 1
    mine = nid[0]
    await ws.send(json.dumps({"id": mine, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(raw)
        if msg.get("id") != mine:
            continue
        if msg.get("error"):
            raise RuntimeError(str(msg["error"])[:200])
        details = (msg.get("result") or {}).get("exceptionDetails") if isinstance(msg.get("result"), dict) else None
        if details:
            raise RuntimeError(str(details.get("text") or "CDP evaluate failed")[:200])
        return _expr_value(msg)


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _raise_if_abort(abort: Optional[asyncio.Event]) -> None:
    if abort is not None and abort.is_set():
        raise asyncio.CancelledError()


def _same_session(page_url: str, href: str) -> bool:
    path = urlsplit(page_url or "").path.rstrip("/") or "/"
    target = (href or "").rstrip("/") or "/"
    return path == target


def _click_js(href: str, token: str) -> str:
    """Click now. The Python side waits first, then this checks the token at click time."""
    return (
        "(function(){const href=%s;const token=%s;"
        "if(window.__gsm2TranscriptToken!==token)return false;"
        "const link=Array.from(document.querySelectorAll('a[href]')).find("
        "(el)=>el.getAttribute('href')===href);"
        "if(!link||window.__gsm2TranscriptToken!==token)return false;"
        "link.click();return true;})()"
    ) % (json.dumps(href), json.dumps(token))


def _arm_click_js(token: str) -> str:
    return "window.__gsm2TranscriptToken=%s" % json.dumps(token)


def _clear_click_js() -> str:
    return "window.__gsm2TranscriptToken=null"


async def _wait_then_click(ws: Any, nid: list[int], href: str, token: str, abort: Optional[asyncio.Event]) -> Any:
    """Wait the guard in Python, then click. Abort during the wait does not send the click."""
    _raise_if_abort(abort)
    if abort is None:
        await asyncio.sleep(CLICK_GUARD_MS / 1000)
    else:
        try:
            await asyncio.wait_for(abort.wait(), timeout=CLICK_GUARD_MS / 1000)
        except asyncio.TimeoutError:
            pass
        else:
            raise asyncio.CancelledError()
    _raise_if_abort(abort)
    return await _cdp_eval(ws, nid, _click_js(href, token))


async def read_voice_turns(
    cdp_http: str,
    timeout_s: float = READ_TIMEOUT_S,
    *,
    abort: Optional[asyncio.Event] = None,
    started_at: Optional[float] = None,
    ended_at: Optional[float] = None,
) -> Any:
    """Read the voice session that started during this call. Never raises CancelledError past the caller.

    Returns turns, or ``{"status": "unmatched"|"ambiguous", ...}``. Does not click
    an unlabeled conversation, and does not click after ``abort`` is set.
    """
    base = _loopback_http(cdp_http)
    if not base:
        raise RuntimeError("CDP URL is not a loopback HTTP endpoint")
    list_url = base if base.endswith("/json/list") else base + "/json/list"

    async def _turns_from(ws: Any, nid: list[int], expression: str = _READ_EXPR) -> list[dict[str, Any]]:
        _raise_if_abort(abort)
        turns = _loads_list(await _cdp_eval(ws, nid, expression))
        return [t for t in turns if isinstance(t, dict)]

    async def _once() -> Any:
        import websockets

        _raise_if_abort(abort)
        targets = await asyncio.to_thread(_http_json, list_url, 4)
        page = _grok_page(targets)
        if page is None:
            raise RuntimeError("no Grok page on a loopback CDP debugger URL")
        ws_url = str(page["webSocketDebuggerUrl"])
        nid = [0]
        async with websockets.connect(ws_url, open_timeout=4, max_size=4_000_000) as ws:
            _raise_if_abort(abort)
            # The desktop shell has no /c/ voice links. Keep the voice-call card
            # for this call; the rest of the mounted history is not saved.
            if _is_desktop_renderer_url(str(page.get("url") or "")):
                raw = await _cdp_eval(ws, nid, _DESKTOP_READ_EXPR)
                _raise_if_abort(abort)
                return scope_desktop_voice(raw, started_at, ended_at)
            sessions = await _cdp_eval(ws, nid, _SESSIONS_EXPR)
            choice = select_voice_session(sessions, started_at, ended_at)
            if choice.get("status") != "chosen":
                return choice
            href = str(choice.get("href") or "")
            if _same_session(str(page.get("url") or ""), href):
                return await _turns_from(ws, nid)
            _raise_if_abort(abort)
            token = secrets.token_hex(8)
            await _cdp_eval(ws, nid, _arm_click_js(token))
            try:
                await _wait_then_click(ws, nid, href, token, abort)
            except asyncio.CancelledError:
                try:
                    await _cdp_eval(ws, nid, _clear_click_js())
                except Exception:
                    LOG.debug("could not clear transcript click token", exc_info=True)
                raise
        await asyncio.sleep(0.6)
        _raise_if_abort(abort)
        targets = await asyncio.to_thread(_http_json, list_url, 4)
        page = _grok_page(targets)
        if page is None:
            raise RuntimeError("Grok page left CDP after opening the voice session")
        async with websockets.connect(str(page["webSocketDebuggerUrl"]), open_timeout=4, max_size=4_000_000) as ws:
            nid = [0]
            for _ in range(8):
                _raise_if_abort(abort)
                turns = await _turns_from(ws, nid)
                if turns:
                    return turns
                await asyncio.sleep(0.4)
        return []

    return await asyncio.wait_for(_once(), timeout=timeout_s)


async def capture_call_transcript(
    directory: Path,
    call_id: str,
    *,
    cdp_http: Optional[str] = None,
    status: Optional[dict[str, Any]] = None,
    read_turns: Optional[Callable[..., Awaitable[Any]]] = None,
    abort: Optional[asyncio.Event] = None,
    started_at: Optional[float] = None,
    ended_at: Optional[float] = None,
) -> dict[str, Any]:
    """Read and save the transcript. Always returns a pointer; never raises."""
    try:
        if abort is not None and abort.is_set():
            return save_transcript_result(
                directory,
                call_id,
                "aborted",
                error="new call started before the transcript read",
            )
        base = cdp_http or cdp_base_from_status(status)
        if not base:
            return save_transcript_result(
                directory,
                call_id,
                "no_cdp",
                error="patchbay status has no loopback CDP endpoint; set GSM2COMPUTER_GROKBOT_CDP",
            )
        if read_turns is None:
            raw = await read_voice_turns(
                base,
                abort=abort,
                started_at=started_at,
                ended_at=ended_at,
            )
        else:
            raw = await read_turns(base)
        document = normalize_turns(raw, call_id)
        return save_transcript_result(
            directory,
            call_id,
            str(document["status"]),
            list(document["turns"]),
            document.get("error"),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.warning("grok transcript read failed call_id=%s: %s", call_id, exc)
        try:
            return save_transcript_result(directory, call_id, "error", error=f"{type(exc).__name__}: {exc}")
        except Exception as write_exc:
            LOG.warning("grok transcript status write failed: %s", write_exc)
            return {
                "status": "error",
                "file": TRANSCRIPT_NAME,
                "path": str(directory / TRANSCRIPT_NAME),
                "turns": 0,
                "error": str(exc),
                "call_id": call_id,
                "preview": [],
            }


def announce_transcript(pointer: dict[str, Any]) -> None:
    """Post ``call_transcript`` with the same webhook helper as ``call_ended``.

    The hub process does not call this. The process that already posted
    ``call_ended`` does, so the URL, key, and spool are the ones that process
    already has. An unset URL stays a no-op inside ``post_alert_event``.
    """
    try:
        from alert_webhook import post_alert_event

        call_id = str(pointer.get("call_id") or "")
        text = (
            f"call_transcript call_id={call_id} "
            f"transcript_status={pointer.get('status')} "
            f"transcript={pointer.get('path') or '-'}"
        )
        payload: dict[str, Any] = {
            "event": "call_transcript",
            "call_id": call_id,
            "transcript_status": pointer.get("status"),
            "transcript": pointer,
            "text": text,
        }
        if "e2e" in call_id:
            payload["e2e"] = True
        post_alert_event(payload)
    except Exception:
        LOG.warning("call_transcript alert failed", exc_info=True)


def _timeout_pointer(pointer: dict[str, Any]) -> dict[str, Any]:
    timed = dict(pointer)
    timed["status"] = "timeout"
    timed["error"] = "transcript.json was not written before the follow-up wait ended"
    return timed


def wait_for_transcript(
    directory: Path,
    timeout_s: float,
    *,
    poll_s: float = 0.25,
) -> Optional[dict[str, Any]]:
    """Poll ``transcript.json`` until it has a status other than ``pending``."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    found: Optional[dict[str, Any]] = None
    while True:
        found = load_saved_pointer(directory)
        if found and str(found.get("status") or "") != "pending":
            return found
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return found
        time.sleep(min(poll_s, remaining))


def post_transcript_followup(
    pointer: dict[str, Any],
    *,
    directory: Optional[Path] = None,
    timeout_s: Optional[float] = None,
    poll_s: float = 0.25,
) -> None:
    """Block until the transcript file is final, then post ``call_transcript``.

    Callers run this off the hangup path. ``call_ended`` has already been posted.
    """
    limit = FOLLOWUP_WAIT_S if timeout_s is None else timeout_s
    final = pointer
    if str(pointer.get("status") or "") == "pending":
        folder = directory
        if folder is None and pointer.get("path"):
            folder = Path(str(pointer["path"])).parent
        found = wait_for_transcript(folder, limit, poll_s=poll_s) if folder is not None else None
        final = found if found else _timeout_pointer(pointer)
    announce_transcript(final)


def schedule_transcript_followup(
    pointer: dict[str, Any],
    *,
    directory: Optional[Path] = None,
    timeout_s: Optional[float] = None,
    poll_s: float = 0.25,
) -> threading.Thread:
    """Post ``call_transcript`` in the background so ``call_ended`` is not delayed."""
    thread = threading.Thread(
        target=post_transcript_followup,
        kwargs={
            "pointer": pointer,
            "directory": directory,
            "timeout_s": timeout_s,
            "poll_s": poll_s,
        },
        name="call-transcript",
        daemon=True,
    )
    thread.start()
    return thread


def pointer_or_aborted(directory: Path, call_id: str) -> dict[str, Any]:
    """The saved pointer, or a new ``aborted`` file when the read never finished."""
    existing = load_saved_pointer(directory)
    if existing is not None:
        return existing
    return save_transcript_result(
        directory,
        call_id,
        "aborted",
        error="new call started before the transcript read finished",
    )


def transcript_for_alert(tap: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pointer for a grokbot tap: the saved file, else ``missing`` when this call was Grok Bot."""
    if not isinstance(tap, dict):
        return None
    directory = str(tap.get("dir") or "").strip()
    if directory:
        found = load_saved_pointer(Path(directory))
        if found:
            return found
    meta = tap.get("meta") if isinstance(tap.get("meta"), dict) else {}
    grok = str(tap.get("id") or "").endswith("-grokbot") or meta.get("mode") == "grokbot"
    embedded = tap.get("transcript")
    if isinstance(embedded, dict) and embedded.get("status"):
        return embedded
    if not grok:
        return None
    return {
        "status": "pending",
        "file": TRANSCRIPT_NAME,
        "path": str(Path(directory) / TRANSCRIPT_NAME) if directory else None,
        "turns": 0,
        "error": "transcript not saved yet; call_transcript follows",
        "call_id": tap.get("id"),
        "preview": [],
    }
