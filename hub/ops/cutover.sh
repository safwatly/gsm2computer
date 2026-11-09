#!/usr/bin/env bash
# Switch cup's hub code, or put the previous tree back.
# Idle gate first: /health busy is false, and the phone's mCallState is 0
# when GSM2COMPUTER_CALL_END_ADB is set on the call-end unit. A failed gate
# exits 1 before any copy, rsync, stop, or restart.
set -euo pipefail

LIVE=${GSM2COMPUTER_CUTOVER_LIVE:-$HOME/gsm2computer-hub}
REPO=${GSM2COMPUTER_CUTOVER_REPO:-$HOME/gsm2computer/hub}
HUB_UNIT=${GSM2COMPUTER_CUTOVER_HUB_UNIT:-gsm2computer-hub.service}
CALL_END_UNIT=${GSM2COMPUTER_CUTOVER_CALL_END_UNIT:-gsm2computer-call-end-watch.service}
E2E_UNIT=${GSM2COMPUTER_CUTOVER_E2E_UNIT:-gsm2computer-openclaw-e2e.service}
WATCHDOG_UNIT=${GSM2COMPUTER_CUTOVER_WATCHDOG_UNIT:-cup-watchdog.service}

usage() {
  echo "usage: hub/ops/cutover.sh apply|rollback" >&2
  exit 2
}

unit_env_get() {
  local unit=$1 key=$2 raw value
  raw=$(systemctl --user show -p Environment -- "$unit" 2>/dev/null || true)
  value=$(printf '%s\n' "$raw" | python3 -c '
import shlex, sys
key = sys.argv[1]
found = ""
for line in sys.stdin:
    line = line.strip()
    if line.startswith("Environment="):
        line = line.split("=", 1)[1]
    if not line:
        continue
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    for part in parts:
        if "=" not in part:
            continue
        name, val = part.split("=", 1)
        if name == key and val.strip():
            found = val.strip()
print(found, end="")
' "$key")
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return 0
  fi
  return 1
}

require_key() {
  local unit=$1 key=$2
  if ! unit_env_get "$unit" "$key" >/dev/null; then
    echo "cutover: $unit is missing $key" >&2
    exit 1
  fi
}

require_private_env() {
  require_key "$HUB_UNIT" GSM2COMPUTER_HUB_HOST
  require_key "$HUB_UNIT" GSM2COMPUTER_HUB_PORT
  require_key "$HUB_UNIT" MOTO_TS_IP
  require_key "$CALL_END_UNIT" GSM2COMPUTER_CALL_END_ADB
  require_key "$CALL_END_UNIT" GSM2COMPUTER_HUB_HTTP
  require_key "$E2E_UNIT" GSM2COMPUTER_E2E_HUB
  require_key "$WATCHDOG_UNIT" CUP_WATCHDOG_HUB
  require_key "$WATCHDOG_UNIT" CUP_WATCHDOG_PIXEL_IP
}

bound_health_url() {
  local host port
  host=$(unit_env_get "$HUB_UNIT" GSM2COMPUTER_HUB_HOST || true)
  port=$(unit_env_get "$HUB_UNIT" GSM2COMPUTER_HUB_PORT || true)
  if [[ -z "$host" ]]; then
    host=${GSM2COMPUTER_HUB_HOST:-}
  fi
  if [[ -z "$port" ]]; then
    port=${GSM2COMPUTER_HUB_PORT:-}
  fi
  if [[ -z "$host" || -z "$port" ]]; then
    echo "cutover: $HUB_UNIT has no GSM2COMPUTER_HUB_HOST and GSM2COMPUTER_HUB_PORT" >&2
    exit 1
  fi
  printf 'http://%s:%s/health' "$host" "$port"
}

require_idle() {
  local url body
  url=$(bound_health_url)
  if ! body=$(curl -fsS --max-time 5 "$url"); then
    echo "cutover: hub unreachable at $url" >&2
    exit 1
  fi
  if ! printf '%s' "$body" | python3 -c '
import json, sys
try:
    body = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
if body.get("busy") is not False:
    sys.exit(1)
'; then
    echo "cutover: hub at $url is not idle (busy must be false)" >&2
    exit 1
  fi

  local serial=""
  serial=$(unit_env_get "$CALL_END_UNIT" GSM2COMPUTER_CALL_END_ADB || true)
  if [[ -z "$serial" ]]; then
    return 0
  fi
  local dump
  if ! dump=$(adb -s "$serial" shell dumpsys telephony.registry); then
    echo "cutover: adb call-state check failed for $serial" >&2
    exit 1
  fi
  if ! grep -q 'mCallState=0' <<<"$dump"; then
    echo "cutover: phone mCallState is not 0" >&2
    exit 1
  fi
  if grep -E -q 'mCallState=[1-9]' <<<"$dump"; then
    echo "cutover: phone mCallState is not 0" >&2
    exit 1
  fi
}

apply_tree() {
  local stamp backup
  if [[ ! -d "$LIVE" ]]; then
    echo "cutover: live dir missing: $LIVE" >&2
    exit 1
  fi
  if [[ ! -d "$REPO" ]]; then
    echo "cutover: repo hub missing: $REPO" >&2
    exit 1
  fi
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  backup="${LIVE}.rollback-${stamp}"
  cp -a "$LIVE" "$backup"
  printf '%s\n' "$backup" >"${LIVE}.rollback-latest"
  rsync -a --delete \
    --exclude state/ --exclude var/ --exclude workspace/ --exclude backups/ \
    --exclude '.venv/' --exclude 'portal.sqlite*' --exclude device-tokens.json \
    --exclude __pycache__/ \
    "$REPO"/ "$LIVE"/
  systemctl --user restart gsm2computer-hub.service
  echo "cutover: applied, rollback tree $backup"
}

rollback_tree() {
  local backup
  if [[ ! -f "${LIVE}.rollback-latest" ]]; then
    echo "cutover: no ${LIVE}.rollback-latest" >&2
    exit 1
  fi
  backup=$(cat "${LIVE}.rollback-latest")
  if [[ ! -d "$backup" ]]; then
    echo "cutover: rollback tree missing: $backup" >&2
    exit 1
  fi
  systemctl --user stop gsm2computer-hub.service
  rm -rf "$LIVE"
  mv "$backup" "$LIVE"
  rm -f "${LIVE}.rollback-latest"
  systemctl --user start gsm2computer-hub.service
  echo "cutover: restored $LIVE"
}

cmd=${1:-}
case "$cmd" in
  apply)
    require_idle
    require_private_env
    apply_tree
    ;;
  rollback)
    require_idle
    rollback_tree
    ;;
  *)
    usage
    ;;
esac
