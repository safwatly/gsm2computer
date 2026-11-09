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
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
adapted=$(mktemp)
GSM2COMPUTER_PUBLIC_HOSTNAME=gsm.ta3leam.com \
GSM2COMPUTER_PUBLIC_BIND=62.83.35.197 \
GSM2COMPUTER_ACME_EMAIL=ops@example.com \
GSM2COMPUTER_PUBLIC_UPSTREAM=127.0.0.1:8790 \
  caddy adapt --config "$ROOT/deploy/cup/Caddyfile" --adapter caddyfile >"$adapted"
python3 - "$adapted" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
servers = cfg["apps"]["http"]["servers"]
listens = [addr for server in servers.values() for addr in server.get("listen") or []]
if listens != ["62.83.35.197:443"]:
    sys.exit(f"caddy listeners are not the public address only: {listens}")
for server in servers.values():
    protocols = server.get("protocols") or []
    if "h3" in protocols:
        sys.exit(f"caddy still offers HTTP/3: {protocols}")
bind_host = (
    cfg.get("apps", {})
    .get("tls", {})
    .get("automation", {})
    .get("policies", [{}])[0]
    .get("issuers", [{}])[0]
    .get("challenges", {})
    .get("bind_host")
)
if bind_host != "62.83.35.197":
    sys.exit(f"acme challenge bind_host is {bind_host}, want 62.83.35.197")
http_challenge = (
    cfg["apps"]["tls"]["automation"]["policies"][0]["issuers"][0]["challenges"].get("http") or {}
)
if not http_challenge.get("disabled"):
    sys.exit("http challenge is still enabled")
print("caddy bind ok listen=62.83.35.197:443 challenge_bind=62.83.35.197 http_challenge=off")
PY
rm -f "$adapted"
if ! grep -q 'data-entrance' "$ROOT"/hub/portal/dist/assets/*.js; then
  echo "served portal dist is missing the call entrance chip; rebuild hub/portal" >&2
  exit 1
fi
echo "portal dist has entrance chip"
