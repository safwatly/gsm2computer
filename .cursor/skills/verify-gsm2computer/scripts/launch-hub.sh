#!/usr/bin/env bash
# Start an isolated gsm2computer hub with both entrances. Does not touch cup.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)
STATE=${GSM2COMPUTER_VERIFY_STATE:-/tmp/gsm2computer-verify}
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
TAILSCALE_PORT=${GSM2COMPUTER_VERIFY_TAILSCALE_PORT:-18787}
PUBLIC_PORT=${GSM2COMPUTER_VERIFY_PUBLIC_PORT:-18790}
mkdir -p "$STATE" "$EVIDENCE"
if [[ -f "$STATE/hub.pid" ]] && kill -0 "$(cat "$STATE/hub.pid")" 2>/dev/null; then
  echo "hub already running pid $(cat "$STATE/hub.pid") ($STATE)"
  exit 0
fi

TOKEN_FILE=$STATE/device-tokens.json
if [[ ! -f "$TOKEN_FILE" ]]; then
  python3 "$ROOT/hub/device_auth.py" generate moto-g84 --file "$TOKEN_FILE" >"$STATE/device.token"
  chmod 600 "$TOKEN_FILE" "$STATE/device.token"
fi
if [[ ! -s "$STATE/device.token" ]]; then
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["devices"][0]["token"])' \
    "$TOKEN_FILE" >"$STATE/device.token"
  chmod 600 "$STATE/device.token"
fi

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/tmp/runtime-$(id -u)}
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
# A dead DBUS_SESSION_BUS_ADDRESS makes WirePlumber drop its PipeWire
# connection on startup. This launcher does not need a session bus.
if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  _bus_path=${DBUS_SESSION_BUS_ADDRESS#unix:path=}
  if [[ "$_bus_path" == "$DBUS_SESSION_BUS_ADDRESS" || ! -S "$_bus_path" ]]; then
    unset DBUS_SESSION_BUS_ADDRESS
  fi
fi
unset _bus_path
if ! pactl info >/dev/null 2>&1; then
  pipewire >"$STATE/pipewire.log" 2>&1 &
  echo $! >"$STATE/pipewire.pid"
  pipewire-pulse >"$STATE/pipewire-pulse.log" 2>&1 &
  echo $! >"$STATE/pipewire-pulse.pid"
  for _ in $(seq 1 50); do
    pactl info >/dev/null 2>&1 && break
    sleep 0.1
  done
fi
pactl info >/dev/null
# pw-cat --target is linked by the session manager. This VM has no
# systemd-logind, and WirePlumber 0.4 exits when its logind plugin fails,
# which leaves every playback stream unlinked. Cup has logind, so the
# override below is only for this stand-in.
if ! pgrep -x wireplumber >/dev/null 2>&1; then
  WP_CFG=$STATE/wireplumber-config
  mkdir -p "$WP_CFG/wireplumber/bluetooth.lua.d"
  cat >"$WP_CFG/wireplumber/bluetooth.lua.d/51-no-logind.lua" <<'EOF'
bluez_monitor.properties["with-logind"] = false
bluez_midi_monitor.properties["with-logind"] = false
bluez_monitor.enabled = false
bluez_midi_monitor.enabled = false
EOF
  XDG_CONFIG_HOME=$WP_CFG wireplumber >"$STATE/wireplumber.log" 2>&1 &
  echo $! >"$STATE/wireplumber.pid"
  sleep 0.4
  if ! kill -0 "$(cat "$STATE/wireplumber.pid")" 2>/dev/null; then
    echo "wireplumber exited; see $STATE/wireplumber.log" >&2
    exit 1
  fi
fi
bash "$ROOT/hub/setup-audio-bus.sh" >"$STATE/audio-bus.log"

export GSM2COMPUTER_HUB_HOST=127.0.0.1
export GSM2COMPUTER_HUB_PORT=$TAILSCALE_PORT
export GSM2COMPUTER_PUBLIC_ENABLE=1
export GSM2COMPUTER_PUBLIC_HOST=127.0.0.1
export GSM2COMPUTER_PUBLIC_PORT=$PUBLIC_PORT
export GSM2COMPUTER_PUBLIC_HOSTNAME=gsm.ta3leam.com
export GSM2COMPUTER_PUBLIC_TOKEN_REQUIRED=1
export GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=0
export GSM2COMPUTER_DEVICE_TOKENS=$TOKEN_FILE
export GSM2COMPUTER_PORTAL_DB=$STATE/portal.sqlite
export GSM2COMPUTER_LINK_GAP_LOG=$STATE/link-gaps.jsonl
export GSM2COMPUTER_OPENCLAW_TALK=off
export GSM2COMPUTER_AUTO_MODE=loopback
export GSM2COMPUTER_HUB_DIR=$ROOT/hub
export GSM2COMPUTER_PW_LINK_TIMEOUT=${GSM2COMPUTER_PW_LINK_TIMEOUT:-8}
cd "$ROOT"
python3 hub/hub.py >"$STATE/hub.log" 2>&1 &
echo $! >"$STATE/hub.pid"

ready=0
for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:${TAILSCALE_PORT}/health" >/dev/null; then
    ready=1
    break
  fi
  sleep 0.1
done
[[ "$ready" == 1 ]] || { echo "hub did not become ready; see $STATE/hub.log" >&2; exit 1; }

cat >"$STATE/runtime.env" <<EOF
ROOT=$ROOT
STATE=$STATE
EVIDENCE=$EVIDENCE
TAILSCALE_PORT=$TAILSCALE_PORT
PUBLIC_PORT=$PUBLIC_PORT
TAILSCALE_URL=http://127.0.0.1:${TAILSCALE_PORT}
PUBLIC_URL=http://127.0.0.1:${PUBLIC_PORT}
TOKEN_FILE=$TOKEN_FILE
DEVICE_TOKEN=$(cat "$STATE/device.token")
DEVICE_ID=moto-g84
EOF
chmod 600 "$STATE/runtime.env"
echo "hub pid $(cat "$STATE/hub.pid") tailscale :$TAILSCALE_PORT public :$PUBLIC_PORT"
