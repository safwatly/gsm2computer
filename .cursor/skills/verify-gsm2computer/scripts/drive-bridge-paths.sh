#!/usr/bin/env bash
# Place one call on the public entrance and one on the Tailscale entrance
# using the bridge's HubConnectionRoutine. Fails unless both succeed.
# Does not touch cup or a phone. Needs the isolated hub (PipeWire null sinks).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
STATE=${GSM2COMPUTER_VERIFY_STATE:-/tmp/gsm2computer-bridge-paths}
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
# Dedicated ports so this does not attach to a hub the rest of the skill
# already started on 18787/18790.
export GSM2COMPUTER_VERIFY_STATE="$STATE"
export GSM2COMPUTER_VERIFY_EVIDENCE="$EVIDENCE"
export GSM2COMPUTER_VERIFY_TAILSCALE_PORT=${GSM2COMPUTER_VERIFY_TAILSCALE_PORT:-18987}
export GSM2COMPUTER_VERIFY_PUBLIC_PORT=${GSM2COMPUTER_VERIFY_PUBLIC_PORT:-18990}
export GSM2COMPUTER_BRIDGE_PATH_RELEASE=1

mkdir -p "$STATE" "$EVIDENCE"
rm -f "$EVIDENCE/bridge-paths.json"

if [[ -f "$STATE/hub.pid" ]] && kill -0 "$(cat "$STATE/hub.pid")" 2>/dev/null; then
  echo "reusing hub pid $(cat "$STATE/hub.pid") ($STATE)"
else
  bash "$ROOT/.cursor/skills/verify-gsm2computer/scripts/launch-hub.sh"
fi

cd "$ROOT"
./gradlew :app:testDebugUnitTest --tests com.gsm2computer.bridge.HubPathReleaseTest --no-daemon

python3 - "$EVIDENCE/bridge-paths.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    report = json.load(handle)
required = ("public", "tailscale", "failover", "relink")
missing = [key for key in required if not report.get(key)]
if not report.get("ok") or missing:
    print(json.dumps(report, indent=2))
    sys.exit(1)
print("bridge paths ok")
for key in required:
    print(f"  {key}: {report[key]}")
PY
