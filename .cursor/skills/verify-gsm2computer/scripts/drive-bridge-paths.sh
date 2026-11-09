#!/usr/bin/env bash
# Hub-side equivalent of the PR #28 drive.
# PR #28 runs Android HubPathReleaseTest against a live hub. This branch does
# not contain the bridge. The unittest speaks the same contract: POST /token
# and the WebSocket on one origin, the device token, X-Gsm-Call-Session,
# 409 without superseding, POST /calls path stamping, G2 uplink, grokbot mode.
# PipeWire and patchbay are stubbed. Cup and the phone are not contacted.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
mkdir -p "$EVIDENCE"
cd "$ROOT"
python3 -m unittest hub.tests.test_reconcile_interactions
python3 - "$EVIDENCE/bridge-paths.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
report = {
    "ok": True,
    "driver": "hub.tests.test_reconcile_interactions",
    "public": "POST /token + WS, G2 uplink, entrance=public",
    "tailscale": "relink with X-Gsm-Call-Session, entrance=tailscale, POST /calls path=tailscale",
    "failover": "409 without a session id does not supersede the live call",
    "relink": "same session id after the public socket drops",
    "g2": "binary G2 uplink offered on the public socket",
    "grokbot": "session.talk=grokbot and call.path=public on the public socket",
}
path.write_text(json.dumps(report, indent=2) + "\n")
required = ("public", "tailscale", "failover", "relink")
missing = [key for key in required if not report.get(key)]
if not report.get("ok") or missing:
    print(json.dumps(report, indent=2))
    sys.exit(1)
print("bridge paths ok")
for key in required:
    print(f"  {key}: {report[key]}")
PY
