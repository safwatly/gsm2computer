#!/usr/bin/env bash
# Stop processes this skill started. Does not delete evidence or flush host nftables.
set -euo pipefail
STATE=${GSM2COMPUTER_VERIFY_STATE:-/tmp/gsm2computer-verify}
stop() {
  local file=$1
  [[ -f "$file" ]] || return 0
  local pid
  pid=$(cat "$file" 2>/dev/null || true)
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}
if [[ -d "$STATE" ]]; then
  stop "$STATE/caddy.pid"
  stop "$STATE/hub-required.pid"
  stop "$STATE/hub.pid"
  stop "$STATE/pipewire-pulse.pid"
  stop "$STATE/wireplumber.pid"
  stop "$STATE/pipewire.pid"
fi
if command -v ip >/dev/null 2>&1; then
  if ip netns list 2>/dev/null | awk '{print $1}' | grep -qx gsm2nft-verify; then
    sudo ip netns del gsm2nft-verify || true
  fi
fi
echo "teardown done (evidence left in place)"
