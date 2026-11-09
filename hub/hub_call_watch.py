"""Hub-side call watcher (no adb needed).
Follows the hub journal: WebSocket connected/disconnected => call start/end.
On end, if the adb watcher did not fire for this call within GRACE_S, posts call_ended (source=hub).
Also posts phone_unreachable / phone_reachable when the Moto's Tailscale peer goes offline > OFFLINE_S."""
import json, os, re, subprocess, threading, time
from datetime import datetime, timezone
from pathlib import Path
from alert_webhook import post_alert_event

STATE = Path.home() / "gsm2computer-call-end-watch" / "state.json"
PEER = os.environ.get("MOTO_TS_IP", "")
GRACE_S = 25
OFFLINE_S = 600
log = lambda *a: print(datetime.now().isoformat(timespec="seconds"), *a, flush=True)

def adb_fired_since(t0):
    try:
        s = json.loads(STATE.read_text()).get("last_fired_at")
        return s and datetime.fromisoformat(s).timestamp() >= t0
    except Exception:
        return False

def on_end(start, tap, reason):
    time.sleep(GRACE_S)
    if adb_fired_since(start):
        log("adb watcher already fired; skip"); return
    now = time.time()
    p = {"event": "call_ended", "source": "hub_call_watch", "call_id": tap or f"hub-{int(start)}",
         "tap_dir": tap, "duration_s": round(now - GRACE_S - start, 1), "close": reason,
         "ended_at_utc": datetime.now(timezone.utc).isoformat(), "note": "adb watcher did not fire (adb offline?)"}
    try:
        post_alert_event(p); log("posted", p)
    except Exception as e:
        log("post failed", e)

def ts_monitor():
    if not PEER:
        log("MOTO_TS_IP unset; phone reachability check off")
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
