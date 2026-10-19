#!/usr/bin/env bash
# Point the bridge at a hub control URL (Cloudflare tunnel, Funnel, Tailscale)
# without typing it into the phone UI.
#
# Writes /data/local/tmp/gsm2computer-hub.url (survives an app-data wipe) and
# delivers com.gsm2computer.bridge.SET_HUB_URL. The receiver requires
# android.permission.DUMP, which adb shell holds. A running bridge applies it
# immediately: SMS on the next poll, call audio on the next call. If the
# process is stopped, this starts the activity so the drop file is loaded.
#
# Usage:
#   ./scripts/set-hub-url.sh 'https://YOUR-TUNNEL.example'
#   ./scripts/set-hub-url.sh -s SERIAL 'https://YOUR-TUNNEL.example'
#   HUB_CONTROL_URL='https://YOUR-TUNNEL.example' ./scripts/set-hub-url.sh
#
# Watch: adb logcat -s GSM2Bridge
#   hub url applied id=....

set -euo pipefail

PKG="com.gsm2computer.bridge"
ACTION="com.gsm2computer.bridge.SET_HUB_URL"
COMPONENT="${PKG}/.config.HubControlReceiver"
DROP="/data/local/tmp/gsm2computer-hub.url"
SERIAL=""
URL="${HUB_CONTROL_URL:-}"

usage() {
  echo "Usage: $0 [-s SERIAL] https://YOUR-TUNNEL.example" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SERIAL="${2:-}"; [[ -n "$SERIAL" ]] || usage; shift 2 ;;
    -h|--help) usage ;;
    *)
      if [[ -n "$URL" ]]; then
        echo "Unexpected arg: $1" >&2
        usage
      fi
      URL="$1"
      shift
      ;;
  esac
done

[[ -n "$URL" ]] || usage

# Keep in sync with HubControlUrl.SAFE in the Android app.
if [[ ! "$URL" =~ ^https?://[A-Za-z0-9._~%-]+(:[0-9]{1,5})?([/?#][A-Za-z0-9._~%:/?#+=&-]*)?$ ]]; then
  echo "URL must be http(s), with no userinfo or shell characters" >&2
  exit 1
fi

ADB=(adb)
[[ -n "$SERIAL" ]] && ADB=(adb -s "$SERIAL")

rest="${URL#*://}"
hostport="${rest%%/*}"
host="${hostport%%:*}"
echo "Applying hub control URL (host ${host})"

was_running=false
if "${ADB[@]}" shell pidof "$PKG" 2>/dev/null | grep -q '[0-9]'; then
  was_running=true
fi

"${ADB[@]}" shell "printf '%s\n' '$URL' > '$DROP' && chmod 644 '$DROP'"

broadcast_cmd="am broadcast -a $ACTION -n $COMPONENT --es url '$URL'"
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

echo "Logcat: adb logcat -s GSM2Bridge"
