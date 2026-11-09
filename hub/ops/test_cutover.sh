#!/usr/bin/env bash
# Prove cutover.sh exits 1 before rsync or systemctl stop/restart when the
# hub is busy or unreachable, when the phone is off-hook, or when a required
# cup env var is missing.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CUTOVER=$ROOT/hub/ops/cutover.sh
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
mkdir -p "$EVIDENCE"
LOG=$EVIDENCE/cutover.txt
: >"$LOG"

fail() {
  echo "FAIL: $*" | tee -a "$LOG" >&2
  exit 1
}

note() {
  echo "$*" | tee -a "$LOG"
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"; [[ -n "${HUB_PID:-}" ]] && kill "$HUB_PID" 2>/dev/null || true' EXIT

BIN=$WORK/bin
mkdir -p "$BIN" "$WORK/live" "$WORK/repo" "$WORK/units"
printf 'repo-marker\n' >"$WORK/repo/marker.txt"
printf 'live-marker\n' >"$WORK/live/marker.txt"

cat >"$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--user" && "${2:-}" == "show" ]]; then
  unit=""
  prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--" ]]; then
      unit=$arg
    fi
    prev=$arg
  done
  cat "$GSM2COMPUTER_CUTOVER_UNIT_DIR/$unit"
  exit 0
fi
printf '%s\n' "$*" >>"$GSM2COMPUTER_CUTOVER_ACTION_LOG"
exit 0
EOF

cat >"$BIN/adb" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${GSM2COMPUTER_CUTOVER_CALL_STATE:-mCallState=0}"
exit 0
EOF

cat >"$BIN/rsync" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'rsync %s\n' "$*" >>"$GSM2COMPUTER_CUTOVER_ACTION_LOG"
exit 0
EOF

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
# Real curl, so a closed port is a failed connection.
exec /usr/bin/curl "$@"
EOF
chmod 755 "$BIN/systemctl" "$BIN/adb" "$BIN/rsync" "$BIN/curl"

write_units() {
  local host=$1 port=$2 moto=${3-100.64.0.8}
  local dir=$GSM2COMPUTER_CUTOVER_UNIT_DIR
  cat >"$dir/gsm2computer-hub.service" <<EOF
Environment=GSM2COMPUTER_HUB_HOST=$host GSM2COMPUTER_HUB_PORT=$port MOTO_TS_IP=$moto
EOF
  cat >"$dir/gsm2computer-call-end-watch.service" <<EOF
Environment=GSM2COMPUTER_CALL_END_ADB=192.0.2.55:5555 GSM2COMPUTER_HUB_HTTP=http://$host:$port
EOF
  cat >"$dir/gsm2computer-openclaw-e2e.service" <<EOF
Environment=GSM2COMPUTER_E2E_HUB=http://$host:$port
EOF
  cat >"$dir/cup-watchdog.service" <<EOF
Environment=CUP_WATCHDOG_HUB=http://$host:$port CUP_WATCHDOG_PIXEL_IP=192.0.2.55
EOF
}

stop_hub() {
  if [[ -n "${HUB_PID:-}" ]]; then
    kill "$HUB_PID" 2>/dev/null || true
    wait "$HUB_PID" 2>/dev/null || true
    HUB_PID=""
  fi
}

start_hub() {
  local busy=$1
  stop_hub
  local fifo=$WORK/hub.out
  rm -f "$fifo"
  mkfifo "$fifo"
  python3 - "$busy" >"$fifo" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
busy = sys.argv[1] == "true"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "busy": busy}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, fmt, *args):
        return

server = HTTPServer(("127.0.0.1", 0), Handler)
print(server.server_address[1], flush=True)
server.serve_forever()
PY
  HUB_PID=$!
  local port=""
  IFS= read -r port <"$fifo"
  if [[ -z "$port" ]]; then
    fail "fake hub did not print a port"
  fi
  printf '%s' "$port" >"$WORK/hub.port"
}

export PATH="$BIN:/usr/bin:/bin"
export GSM2COMPUTER_CUTOVER_LIVE=$WORK/live
export GSM2COMPUTER_CUTOVER_REPO=$WORK/repo
export GSM2COMPUTER_CUTOVER_UNIT_DIR=$WORK/units
export GSM2COMPUTER_CUTOVER_ACTION_LOG=$WORK/actions
export HOME=$WORK/home
mkdir -p "$HOME"

run_cutover() {
  set +e
  bash "$CUTOVER" "$1" >"$WORK/out" 2>"$WORK/err"
  local rc=$?
  set -e
  printf '%s' "$rc"
}

assert_no_mutate() {
  if [[ -s "$GSM2COMPUTER_CUTOVER_ACTION_LOG" ]]; then
    fail "mutated before the gate: $(cat "$GSM2COMPUTER_CUTOVER_ACTION_LOG")"
  fi
  if compgen -G "$WORK/live.rollback-*" >/dev/null; then
    fail "rollback tree was created"
  fi
}

: >"$GSM2COMPUTER_CUTOVER_ACTION_LOG"

note "busy true"
start_hub true
PORT=$(cat "$WORK/hub.port")
write_units 127.0.0.1 "$PORT"
rc=$(run_cutover apply)
[[ "$rc" -eq 1 ]] || fail "busy apply rc=$rc $(cat "$WORK/err")"
grep -q 'not idle' "$WORK/err" || fail "busy apply did not say not idle: $(cat "$WORK/err")"
assert_no_mutate
note "  apply aborted rc=1"
kill "$HUB_PID" 2>/dev/null || true
wait "$HUB_PID" 2>/dev/null || true
HUB_PID=""

note "unreachable"
write_units 127.0.0.1 1
rc=$(run_cutover apply)
[[ "$rc" -eq 1 ]] || fail "unreachable apply rc=$rc $(cat "$WORK/err")"
grep -q 'unreachable' "$WORK/err" || fail "unreachable apply did not say unreachable: $(cat "$WORK/err")"
assert_no_mutate
note "  apply aborted rc=1"
rc=$(run_cutover rollback)
[[ "$rc" -eq 1 ]] || fail "unreachable rollback rc=$rc $(cat "$WORK/err")"
grep -q 'unreachable' "$WORK/err" || fail "unreachable rollback did not say unreachable"
assert_no_mutate
note "  rollback aborted rc=1"

note "phone off-hook"
start_hub false
PORT=$(cat "$WORK/hub.port")
write_units 127.0.0.1 "$PORT"
export GSM2COMPUTER_CUTOVER_CALL_STATE='mCallState=2'
rc=$(run_cutover apply)
[[ "$rc" -eq 1 ]] || fail "off-hook apply rc=$rc $(cat "$WORK/err")"
grep -q 'mCallState is not 0' "$WORK/err" || fail "off-hook message: $(cat "$WORK/err")"
assert_no_mutate
note "  apply aborted rc=1"
export GSM2COMPUTER_CUTOVER_CALL_STATE='mCallState=0'
kill "$HUB_PID" 2>/dev/null || true
wait "$HUB_PID" 2>/dev/null || true
HUB_PID=""

note "missing MOTO_TS_IP"
start_hub false
PORT=$(cat "$WORK/hub.port")
write_units 127.0.0.1 "$PORT" ""
rc=$(run_cutover apply)
[[ "$rc" -eq 1 ]] || fail "missing env rc=$rc $(cat "$WORK/err")"
grep -q 'missing MOTO_TS_IP' "$WORK/err" || fail "missing env message: $(cat "$WORK/err")"
assert_no_mutate
note "  apply aborted rc=1"
kill "$HUB_PID" 2>/dev/null || true
wait "$HUB_PID" 2>/dev/null || true
HUB_PID=""

note "idle apply"
start_hub false
PORT=$(cat "$WORK/hub.port")
write_units 127.0.0.1 "$PORT"
rc=$(run_cutover apply)
[[ "$rc" -eq 0 ]] || fail "idle apply rc=$rc $(cat "$WORK/err")"
grep -q 'rsync ' "$GSM2COMPUTER_CUTOVER_ACTION_LOG" || fail "idle apply did not rsync"
grep -q 'restart gsm2computer-hub.service' "$GSM2COMPUTER_CUTOVER_ACTION_LOG" || fail "idle apply did not restart"
note "  apply rc=0 after the gate"

note "busy rollback"
: >"$GSM2COMPUTER_CUTOVER_ACTION_LOG"
start_hub true
PORT=$(cat "$WORK/hub.port")
write_units 127.0.0.1 "$PORT"
rc=$(run_cutover rollback)
[[ "$rc" -eq 1 ]] || fail "busy rollback rc=$rc $(cat "$WORK/err")"
grep -q 'not idle' "$WORK/err" || fail "busy rollback message: $(cat "$WORK/err")"
if grep -q 'stop gsm2computer-hub' "$GSM2COMPUTER_CUTOVER_ACTION_LOG"; then
  fail "busy rollback stopped the unit"
fi
note "  rollback aborted rc=1"

note "cutover ok"
