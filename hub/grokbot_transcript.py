"""Save a Grok Bot voice-session transcript after the call has ended.

The live session is never clicked. ``patchbay off`` runs first; only then does
this module open the newest voice-session entry on the Grok page and read both
sides. A failure is written as ``transcript.json`` with ``status`` and ``error``
and is not raised to the call or the alert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

LOG = logging.getLogger("gsm2computer-hub.grokbot")

TRANSCRIPT_NAME = "transcript.json"
READ_TIMEOUT_S = float(os.environ.get("GSM2COMPUTER_GROKBOT_TRANSCRIPT_TIMEOUT_S", "12"))

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
function gsm2VoiceTurns(root) {
  const doc = root || document;
  const nodes = Array.from(doc.querySelectorAll(
    '[data-testid="user-message"], [data-testid="assistant-message"], div.message-bubble'
  ));
  const outer = nodes.filter((node) => !nodes.some((other) => other !== node && node.contains && node.contains(other)));
  const turns = [];
  for (const node of outer) {
    const testid = String((node.getAttribute && node.getAttribute("data-testid")) || "").toLowerCase();
    const role = String(
      (node.getAttribute && (node.getAttribute("data-role") || node.getAttribute("data-author"))) || ""
    ).toLowerCase();
    let speaker = "unknown";
    if (testid === "user-message" || /user|caller|human/.test(role)) speaker = "caller";
    else if (testid === "assistant-message" || /assistant|grok|suzie|bot/.test(role)) speaker = "assistant";
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
function gsm2VoiceSessionHref(root) {
  const doc = root || document;
  const links = Array.from(doc.querySelectorAll('a[href^="/c/"]')).filter(gsm2Visible);
  const voice = /voice|call|phone/i;
  const start = /^(start|new|begin)\b|start voice|new voice/i;
  const labeled = links.filter((el) => {
    const label = String(el.innerText || "") + " " + String(el.getAttribute("aria-label") || "");
    return voice.test(label) && !start.test(label);
  });
  const pool = labeled.length ? labeled : links;
  if (!pool.length) return null;
  return pool[0].getAttribute("href");
}
"""

_READ_EXPR = VOICE_DOM_JS + "\ngsm2VoiceTurns(document)"
_HREF_EXPR = VOICE_DOM_JS + "\ngsm2VoiceSessionHref(document)"


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


def _speaker(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"caller", "user", "human", "you"}:
        return "caller"
    if text in {"assistant", "grok", "suzie", "bot", "model"}:
        return "assistant"
    return text or "unknown"


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
    """Normalize CDP nodes or already-shaped turns. Empty input is status ``empty``."""
    rows: list[Any]
    if isinstance(raw, dict):
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
            else:
                speaker = "unknown"
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        turns.append(
            {
                "speaker": _speaker(speaker),
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
    levels = directory / "levels.json"
    if not levels.is_file():
        return
    try:
        data = json.loads(levels.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    data["transcript"] = pointer
    levels.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
        url = str(page.get("url") or "").lower()
        if "grok.com" in url or "grok.x.ai" in url or "x.com/i/grok" in url:
            if page.get("webSocketDebuggerUrl"):
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


async def read_voice_turns(cdp_http: str, timeout_s: float = READ_TIMEOUT_S) -> list[dict[str, Any]]:
    """Open the newest voice session if the transcript is not already on screen, then read it."""
    base = _loopback_http(cdp_http)
    if not base:
        raise RuntimeError("CDP URL is not a loopback HTTP endpoint")
    list_url = base if base.endswith("/json/list") else base + "/json/list"

    async def _once() -> list[dict[str, Any]]:
        import websockets

        targets = await asyncio.to_thread(_http_json, list_url, 4)
        page = _grok_page(targets)
        if page is None:
            raise RuntimeError("no Grok page on CDP")
        ws_url = str(page["webSocketDebuggerUrl"])
        nid = [0]
        async with websockets.connect(ws_url, open_timeout=4, max_size=4_000_000) as ws:
            turns = _loads_list(await _cdp_eval(ws, nid, _READ_EXPR))
            if turns:
                return [t for t in turns if isinstance(t, dict)]
            href = await _cdp_eval(ws, nid, _HREF_EXPR)
            if not isinstance(href, str) or not href.startswith("/"):
                return []
            click = (
                "(function(){const href=%s;const link=Array.from(document.querySelectorAll('a[href]')).find("
                "(el)=>el.getAttribute('href')===href);if(!link)return false;link.click();return true;})()"
            ) % json.dumps(str(href))
            await _cdp_eval(ws, nid, click)
        await asyncio.sleep(0.6)
        targets = await asyncio.to_thread(_http_json, list_url, 4)
        page = _grok_page(targets)
        if page is None:
            raise RuntimeError("Grok page left CDP after opening the voice session")
        async with websockets.connect(str(page["webSocketDebuggerUrl"]), open_timeout=4, max_size=4_000_000) as ws:
            nid = [0]
            for _ in range(8):
                turns = _loads_list(await _cdp_eval(ws, nid, _READ_EXPR))
                if turns:
                    return [t for t in turns if isinstance(t, dict)]
                await asyncio.sleep(0.4)
        return []

    return await asyncio.wait_for(_once(), timeout=timeout_s)


async def capture_call_transcript(
    directory: Path,
    call_id: str,
    *,
    cdp_http: Optional[str] = None,
    status: Optional[dict[str, Any]] = None,
    read_turns: Optional[Callable[[str], Awaitable[Any]]] = None,
) -> dict[str, Any]:
    """Read and save the transcript. Always returns a pointer; never raises."""
    try:
        base = cdp_http or cdp_base_from_status(status)
        if not base:
            return save_transcript_result(
                directory,
                call_id,
                "no_cdp",
                error="patchbay status has no loopback CDP endpoint; set GSM2COMPUTER_GROKBOT_CDP",
            )
        reader = read_turns or read_voice_turns
        raw = await reader(base)
        document = normalize_turns(raw, call_id)
        return save_transcript_result(
            directory,
            call_id,
            str(document["status"]),
            list(document["turns"]),
            document.get("error"),
        )
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
        "status": "missing",
        "file": TRANSCRIPT_NAME,
        "path": str(Path(directory) / TRANSCRIPT_NAME) if directory else None,
        "turns": 0,
        "error": None,
        "call_id": tap.get("id"),
        "preview": [],
    }
