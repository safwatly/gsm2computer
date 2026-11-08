#!/usr/bin/env bash
# Read-only: is the isolated hub worth driving?
set -euo pipefail
STATE=${GSM2COMPUTER_VERIFY_STATE:-/tmp/gsm2computer-verify}
# shellcheck disable=SC1091
source "$STATE/runtime.env"
python3 - "$TAILSCALE_URL" "$PUBLIC_URL" "$STATE/hub.pid" <<'PY'
import json, sys, urllib.error, urllib.request
tailscale, public, pidfile = sys.argv[1:]
pid = open(pidfile, encoding="utf-8").read().strip()
if not pid:
    sys.exit("hub pid file empty")
try:
    with urllib.request.urlopen(tailscale + "/health", timeout=3) as resp:
        health = json.loads(resp.read().decode())
except Exception as exc:
    sys.exit(f"tailscale /health failed: {exc}")
names = {item["name"] for item in health.get("entrances") or []}
if not health.get("ok") or names != {"public", "tailscale"}:
    sys.exit(f"entrances not ready: {names}")
public_req = urllib.request.Request(public + "/health")
try:
    urllib.request.urlopen(public_req, timeout=3)
    sys.exit("public /health accepted a request with no device token")
except urllib.error.HTTPError as exc:
    if exc.code != 401:
        sys.exit(f"public /health returned {exc.code}, want 401")
print(f"doctor ok hub_pid={pid} entrances={sorted(names)} public_without_token=401")
PY
