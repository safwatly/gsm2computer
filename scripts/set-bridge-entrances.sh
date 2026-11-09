#!/usr/bin/env bash
# Provision the Moto's ordered hub entrances and device token.
#
# Build 344 saved hub_control_url and did not save hub_endpoints or
# device_token. This script writes /data/local/tmp/gsm2computer-hub.url as
# root. The bridge applies that file on boot and on MY_PACKAGE_REPLACED.
# It does not edit gsm2computer.xml.
#
# The token is read from a file and is not printed.
#
#   DEVICE_TOKEN_FILE=/path/to/token ./scripts/set-bridge-entrances.sh -s SERIAL
#
# The token file is one line. Characters outside [A-Za-z0-9._~+/=-] are rejected.
# Default entrances, public then Tailscale:
#   wss://gsm.ta3leam.com/
#   ws://100.113.222.47:8787/
#
# Repeat --endpoint to replace that list. Order is the walk order.
# Watch: adb logcat -s GSM2Bridge
#   hub drop applied id=.... endpoints=2 token=set

set -euo pipefail

PKG="com.gsm2computer.bridge"
ACTION="com.gsm2computer.bridge.SET_HUB_URL"
COMPONENT="${PKG}/.config.HubControlReceiver"
DROP="/data/local/tmp/gsm2computer-hub.url"
PART="${DROP}.part"
SERIAL=""
TOKEN_FILE="${DEVICE_TOKEN_FILE:-}"
ENDPOINTS=()

usage() {
  echo "Usage: DEVICE_TOKEN_FILE=FILE $0 [-s SERIAL] [--endpoint URL ...]" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SERIAL="${2:-}"; [[ -n "$SERIAL" ]] || usage; shift 2 ;;
    --endpoint) ENDPOINTS+=("${2:-}"); [[ -n "${ENDPOINTS[-1]}" ]] || usage; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unexpected arg" >&2; usage ;;
  esac
done

if [[ -z "$TOKEN_FILE" || ! -f "$TOKEN_FILE" ]]; then
  echo "Set DEVICE_TOKEN_FILE to a file that contains the device token." >&2
  exit 1
fi

set +x
device_token_value="$(tr -d '\r\n' < "$TOKEN_FILE")"
if [[ -z "$device_token_value" ]]; then
  echo "token file is empty" >&2
  exit 1
fi
if [[ ! "$device_token_value" =~ ^[A-Za-z0-9._~+/=-]{1,200}$ ]]; then
  echo "token file has characters the bridge will reject" >&2
  exit 1
fi

use_default_entrances=false
if [[ ${#ENDPOINTS[@]} -eq 0 ]]; then
  use_default_entrances=true
  ENDPOINTS=("wss://gsm.ta3leam.com/" "ws://100.113.222.47:8787/")
fi

endpoint_re='^(https?|wss?)://[A-Za-z0-9._~%-]+(:[0-9]{1,5})?([/?#][A-Za-z0-9._~%:/?#+=&-]*)?$'
for endpoint in "${ENDPOINTS[@]}"; do
  if [[ ! "$endpoint" =~ $endpoint_re ]]; then
    echo "endpoint rejected" >&2
    exit 1
  fi
done

umask 077
tmp="$(mktemp)"
{
  printf '%s\n' '# gsm2computer hub entrances'
  if [[ "$use_default_entrances" == true ]]; then
    printf '%s\n' 'hub_control_url=http://100.113.222.47:8787'
  fi
  for endpoint in "${ENDPOINTS[@]}"; do
    printf 'hub_endpoints=%s\n' "$endpoint"
  done
  printf 'device_token=%s\n' "$device_token_value"
} > "$tmp"
unset device_token_value

ADB=(adb)
[[ -n "$SERIAL" ]] && ADB=(adb -s "$SERIAL")

was_running=false
if "${ADB[@]}" shell pidof "$PKG" 2>/dev/null | grep -q '[0-9]'; then
  was_running=true
fi

"${ADB[@]}" push "$tmp" "$PART" >/dev/null
rm -f "$tmp"

if ! "${ADB[@]}" shell su -c "mv '$PART' '$DROP' && chmod 644 '$DROP'"; then
  "${ADB[@]}" shell "rm -f '$PART'" >/dev/null 2>&1 || true
  echo "root install of the drop file failed" >&2
  exit 1
fi

broadcast_cmd="am broadcast -a $ACTION -n $COMPONENT"
out="$("${ADB[@]}" shell "$broadcast_cmd" 2>&1 || true)"
if echo "$out" | grep -qi 'Permission Denial'; then
  echo "shell broadcast denied; retrying as root"
  out="$("${ADB[@]}" shell su -c "$broadcast_cmd" 2>&1 || true)"
fi
echo "$out"

if [[ "$was_running" != true ]]; then
  echo "Bridge process was stopped; starting it"
  "${ADB[@]}" shell su -c "am start -n ${PKG}/.MainActivity --ez mic_capability_relaunch true --activity-brought-to-front" || true
fi

echo "hub entrances written (${#ENDPOINTS[@]} endpoints, device token set)"
echo "Logcat: adb logcat -s GSM2Bridge"
