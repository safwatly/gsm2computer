"""Hub-side call watcher (no adb needed).
Follows the hub journal: WebSocket connected/disconnected => call start/end.
On end, if the adb watcher did not fire for this call within GRACE_S, posts call_ended (source=hub).
Also posts phone_unreachable / phone_reachable when the Moto's Tailscale peer goes offline > OFFLINE_S."""
import json, os, re, subprocess, threading, time
from datetime import datetime, timezone
from pathlib import Path
from alert_webhook import post_alert_event

STATE = Path.home() / "gsm2computer-call-end-watch" / "state.json"


def host_of_adb(serial: str) -> str:
    """Host from an adb serial. `192.0.2.8:5555` -> `192.0.2.8`. A bare host stays."""
    text = (serial or "").strip()
    host, sep, port = text.rpartition(":")
    if sep and port.isdigit():
        return host.strip().strip("[]")
    return text


def phone_peer(environ: dict | None = None) -> str:
    """Tailscale address of the phone.

    `MOTO_TS_IP` wins. Otherwise the host part of `GSM2COMPUTER_CALL_END_ADB`
    on this process, which is how the cup hub-call-watch unit names the phone.
    """
    env = os.environ if environ is None else environ
    explicit = (env.get("MOTO_TS_IP") or "").strip()
    if explicit:
        return explicit
    return host_of_adb(env.get("GSM2COMPUTER_CALL_END_ADB") or "")


PEER = phone_peer()
GRACE_S = 25
OFFLINE_S = 600
log = lambda *a: print(datetime.now().isoformat(timespec="seconds"), *a, flush=True)

def adb_fired_since(t0):
    try:
        s = json.loads(STATE.read_text()).get("last_fired_at")
        return s and datetime.fromisoformat(s).timestamp() >= t0
    except Exception:
        return False

def transcript_pointer(tap):
    """Read transcript.json next to the call recordings. Missing file is not an error."""
    if not tap or "/" in tap or "\\" in tap or tap in {".", ".."}:
        return None
    try:
        from grokbot_transcript import load_saved_pointer

        root = Path(os.environ.get("GSM2COMPUTER_CALL_RECORD_DIR", str(Path.home() / "gsm2computer-calls")))
        return load_saved_pointer(root / tap)
    except Exception as exc:
        log("transcript pointer failed", exc)
        return None


def _attach_assistant_silence(payload, tap):
    """Copy the post-switch silence assessment from levels.json onto call_ended."""
    if not tap or "/" in tap or "\\" in tap or tap in {".", ".."}:
        return
    path = Path(os.environ.get("GSM2COMPUTER_CALL_RECORD_DIR", str(Path.home() / "gsm2computer-calls"))) / tap / "levels.json"
    try:
        levels = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(levels, dict):
        return
    meta = levels.get("meta") if isinstance(levels.get("meta"), dict) else {}
    if "max_outq" in meta:
        try:
            payload["max_outq"] = int(meta.get("max_outq") or 0)
            payload["stall_count"] = int(meta.get("stall_count") or 0)
        except (TypeError, ValueError):
            pass
    try:
        from call_tap import assistant_silence_from_health

        found = assistant_silence_from_health({"last_call_tap": levels})
    except Exception as exc:
        log("assistant silence read failed", exc)
        return
    if not found:
        return
    payload["assistant_silent"] = bool(found.get("assistant_silent"))
    stats = found.get("assistant_silent_stats")
    payload["assistant_silent_stats"] = stats if isinstance(stats, dict) else {}
    if found.get("grok_page"):
        payload["grok_page"] = found["grok_page"]


def on_end(start, tap, reason):
    time.sleep(GRACE_S)
    if adb_fired_since(start):
        log("adb watcher already fired; skip"); return
    now = time.time()
    pointer = transcript_pointer(tap)
    p = {"event": "call_ended", "source": "hub_call_watch", "call_id": tap or f"hub-{int(start)}",
         "tap_dir": tap, "duration_s": round(now - GRACE_S - start, 1), "close": reason,
         "ended_at_utc": datetime.now(timezone.utc).isoformat(), "note": "adb watcher did not fire (adb offline?)"}
    if pointer:
        p["transcript"] = pointer
        p["transcript_status"] = pointer.get("status")
    elif str(tap or "").endswith("-grokbot"):
        p["transcript"] = {
            "status": "pending",
            "file": "transcript.json",
            "turns": 0,
            "error": "transcript not saved yet; call_transcript follows",
            "call_id": tap,
            "preview": [],
        }
        p["transcript_status"] = "pending"
    _attach_assistant_silence(p, tap)
    try:
        post_alert_event(p); log("posted", p)
    except Exception as e:
        log("post failed", e)
        return
    transcript = p.get("transcript")
    if not isinstance(transcript, dict):
        return
    # This process posted call_ended, so it posts call_transcript with the same
    # webhook env and spool. When adb already fired, call_end_watch owns both.
    folder = None
    if tap and "/" not in tap and "\\" not in tap and tap not in {".", ".."}:
        root = Path(os.environ.get("GSM2COMPUTER_CALL_RECORD_DIR", str(Path.home() / "gsm2computer-calls")))
        folder = root / tap
    try:
        from grokbot_transcript import post_transcript_followup

        post_transcript_followup(transcript, directory=folder)
    except Exception as e:
        log("transcript follow-up failed", e)

def ts_monitor():
    if not PEER:
        log("phone address unset (MOTO_TS_IP or GSM2COMPUTER_CALL_END_ADB); reachability check off")
        return
    off_since, alerted = None, False
    while True:
        try:
            out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=15).stdout
            online = any(PEER in (pe.get("TailscaleIPs") or []) and pe.get("Online")
                         for pe in json.loads(out).get("Peer", {}).values())
        except Exception:
            online = None
        if online is False:
            off_since = off_since or time.time()
            if not alerted and time.time() - off_since > OFFLINE_S:
                post_alert_event({"event": "phone_unreachable", "peer": PEER, "offline_s": int(time.time() - off_since)}); alerted = True; log("phone_unreachable")
        elif online:
            if alerted:
                post_alert_event({"event": "phone_reachable", "peer": PEER}); log("phone_reachable")
            off_since, alerted = None, False
        time.sleep(30)

def main():
    threading.Thread(target=ts_monitor, daemon=True).start()
    p = subprocess.Popen(["journalctl", "--user", "-u", "gsm2computer-hub", "-f", "-n", "0", "-o", "cat"],
                         stdout=subprocess.PIPE, text=True)
    start = tap = reason = None
    for line in p.stdout:
        if "WebSocket connected" in line and "loopback=False" in line and "e2e=False" in line:
            start, tap, reason = time.time(), None, None; log("call start")
        elif start and "call tap dir" in line:
            tap = line.strip().rsplit("/", 1)[-1]
        elif start and "ws close" in line:
            m = re.search(r"reason=(.*?) initiator", line); reason = m and m.group(1)
        elif start and "WebSocket disconnected" in line:
            log("call end", tap, reason)
            threading.Thread(target=on_end, args=(start, tap, reason), daemon=True).start()
            start = None

if __name__ == "__main__":
    main()
