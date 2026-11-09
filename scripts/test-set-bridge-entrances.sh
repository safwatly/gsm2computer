#!/usr/bin/env bash
# The ops script reads the token from a file, writes it into the drop file,
# and does not print it or put it on an adb command line.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN_VALUE="unit-token-not-for-log"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

printf '%s' "$TOKEN_VALUE" > "$WORKDIR/token"
mkdir -p "$WORKDIR/bin" "$WORKDIR/pushed"

cat > "$WORKDIR/bin/adb" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-s" ]]; then
  args=("${args[@]:2}")
fi
printf '%s\n' "${args[*]}" >> "${ADB_LOG:?}"
case "${args[0]}" in
  push)
    cp "${args[1]}" "${ADB_PUSHED:?}"
    ;;
  shell)
    if [[ "${args[*]}" == *"rm -f"* ]]; then
      exit 0
    fi
    printf '%s\n' "Broadcast completed"
    ;;
  *)
    echo "unexpected adb: ${args[*]}" >&2
    exit 1
    ;;
esac
EOF
chmod 755 "$WORKDIR/bin/adb"

export ADB_LOG="$WORKDIR/adb.log"
export ADB_PUSHED="$WORKDIR/pushed/drop"
export PATH="$WORKDIR/bin:$PATH"
export DEVICE_TOKEN_FILE="$WORKDIR/token"

set +e
output="$("$ROOT/scripts/set-bridge-entrances.sh" -s moto 2>&1)"
status=$?
set -e
if [[ "$status" -ne 0 ]]; then
  echo "set-bridge-entrances.sh failed: $status" >&2
  printf '%s\n' "$output" >&2
  exit 1
fi

if printf '%s' "$output" | grep -q "$TOKEN_VALUE"; then
  echo "token was printed" >&2
  exit 1
fi
if grep -q "$TOKEN_VALUE" "$ADB_LOG"; then
  echo "token was on an adb command line" >&2
  exit 1
fi
if ! grep -q "su -c mv" "$ADB_LOG"; then
  echo "drop file was not installed with su" >&2
  exit 1
fi
if ! grep -q "am broadcast -a com.gsm2computer.bridge.SET_HUB_URL" "$ADB_LOG"; then
  echo "broadcast missing" >&2
  exit 1
fi
if grep -q -- "--es url" "$ADB_LOG"; then
  echo "broadcast carried a url extra; the receiver would ignore the drop file" >&2
  exit 1
fi

drop="$(cat "$ADB_PUSHED")"
if ! printf '%s' "$drop" | grep -q 'hub_endpoints=wss://gsm.ta3leam.com/'; then
  echo "public entrance missing" >&2
  exit 1
fi
if ! printf '%s' "$drop" | grep -q 'hub_endpoints=ws://100.113.222.47:8787/'; then
  echo "tailscale entrance missing" >&2
  exit 1
fi
public_line="$(printf '%s\n' "$drop" | grep -n 'hub_endpoints=wss://gsm.ta3leam.com/' | cut -d: -f1)"
tail_line="$(printf '%s\n' "$drop" | grep -n 'hub_endpoints=ws://100.113.222.47:8787/' | cut -d: -f1)"
if [[ "$public_line" -ge "$tail_line" ]]; then
  echo "entrances are out of order" >&2
  exit 1
fi
if ! printf '%s' "$drop" | grep -q "device_token=${TOKEN_VALUE}"; then
  echo "drop file is missing the token" >&2
  exit 1
fi
if ! printf '%s' "$output" | grep -q "device token set"; then
  echo "script did not report that a token was set" >&2
  exit 1
fi

echo "set-bridge-entrances.sh keeps the token off stdout and the adb command line"
