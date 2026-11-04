#!/usr/bin/env bash
# Isolated gsm2computer hub for cloud-agent verification.
# Launch, doctor, and stop one process by the pid this script recorded.
# Does not delete the data directory, the log, or captured evidence.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../../../.." && pwd)
POINTER=/tmp/gsm2computer-verify.current

usage() {
  echo "usage: instance.sh launch|doctor|stop" >&2
  exit 2
}

resolve_home() {
  if [[ -n "${GSM2COMPUTER_VERIFY_HOME:-}" ]]; then
    printf '%s\n' "$GSM2COMPUTER_VERIFY_HOME"
    return
  fi
  if [[ -f "$POINTER" ]]; then
    head -n 1 "$POINTER"
    return
  fi
  printf '%s\n' /tmp/gsm2computer-verify
}

load_instance() {
  local home=$1
  if [[ ! -f "$home/instance.env" ]]; then
    echo "no instance.env in $home — run launch first" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source "$home/instance.env"
}

cmdline_of() {
  local pid=$1
  if [[ ! -r "/proc/$pid/cmdline" ]]; then
    return 1
  fi
  tr '\0' ' ' < "/proc/$pid/cmdline"
}

assert_hub_pid() {
  local pid=$1
  local cmd
  cmd=$(cmdline_of "$pid" || true)
  case "$cmd" in
    *python*hub.py*) return 0 ;;
  esac
  echo "pid $pid is not this repo's hub.py (cmdline: ${cmd:-missing})" >&2
  return 1
}

port_in_use() {
  python3 -c 'import socket,sys
s=socket.socket()
s.settimeout(0.3)
rc=s.connect_ex(("127.0.0.1", int(sys.argv[1])))
s.close()
sys.exit(0 if rc==0 else 1)' "$1"
}

launch() {
  local home port base log pid
  home=$(resolve_home)
  port=${GSM2COMPUTER_HUB_PORT:-18787}
  base="http://127.0.0.1:${port}"
  mkdir -p "$home/calls"
  if [[ -f "$home/instance.env" ]]; then
    # shellcheck disable=SC1091
    source "$home/instance.env"
    if [[ -n "${GSM2COMPUTER_HUB_PID:-}" ]] && kill -0 "$GSM2COMPUTER_HUB_PID" 2>/dev/null; then
      if assert_hub_pid "$GSM2COMPUTER_HUB_PID"; then
        echo "already running pid $GSM2COMPUTER_HUB_PID at $GSM2COMPUTER_HUB_BASE" >&2
        exit 1
      fi
    fi
  fi
  if port_in_use "$port"; then
    echo "127.0.0.1:$port is already in use; pick another GSM2COMPUTER_HUB_PORT and GSM2COMPUTER_VERIFY_HOME" >&2
    exit 1
  fi
  if [[ ! -f "$REPO_ROOT/hub/hub.py" ]]; then
    echo "missing $REPO_ROOT/hub/hub.py" >&2
    exit 1
  fi
  log="$home/hub.log"
  : >"$log"
  # Record the python pid, not a wrapping subshell. A subshell pid would
  # leave hub.py running after stop.
  pushd "$REPO_ROOT/hub" >/dev/null
  env \
    PYTHONUNBUFFERED=1 \
    GSM2COMPUTER_HUB_HOST=127.0.0.1 \
    GSM2COMPUTER_HUB_PORT="$port" \
    GSM2COMPUTER_HUB_DIR="$REPO_ROOT/hub" \
    GSM2COMPUTER_PORTAL_DB="$home/portal.sqlite" \
    GSM2COMPUTER_CALL_RECORD_DIR="$home/calls" \
    GSM2COMPUTER_LINK_GAP_LOG="$home/link-gaps.jsonl" \
    GSM2COMPUTER_OPENCLAW_TALK=off \
    GSM2COMPUTER_AUTO_MODE= \
    GSM2COMPUTER_ALERT_WEBHOOK_URL= \
    GSM2COMPUTER_E2E_AFTER_SILENT=0 \
    python3 hub.py >>"$log" 2>&1 &
  pid=$!
  popd >/dev/null
  {
    printf 'GSM2COMPUTER_VERIFY_HOME=%q\n' "$home"
    printf 'GSM2COMPUTER_HUB_PORT=%q\n' "$port"
    printf 'GSM2COMPUTER_HUB_BASE=%q\n' "$base"
    printf 'GSM2COMPUTER_HUB_PID=%q\n' "$pid"
    printf 'GSM2COMPUTER_HUB_LOG=%q\n' "$log"
    printf 'GSM2COMPUTER_PORTAL_DB=%q\n' "$home/portal.sqlite"
  } >"$home/instance.env"
  printf '%s\n' "$home" >"$POINTER"

  local i
  for i in $(seq 1 50); do
    if curl -sf -o "$home/health-ready.json" "$base/health"; then
      echo "ready $base pid $pid home $home"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "hub exited before /health" >&2
      tail -n 60 "$log" >&2 || true
      exit 1
    fi
    sleep 0.2
  done
  echo "timed out waiting for $base/health" >&2
  tail -n 60 "$log" >&2 || true
  kill "$pid" 2>/dev/null || true
  exit 1
}

doctor() {
  local home
  home=$(resolve_home)
  load_instance "$home"
  if [[ -z "${GSM2COMPUTER_HUB_PID:-}" ]]; then
    echo "not worth driving: instance.env has no pid" >&2
    exit 1
  fi
  if ! kill -0 "$GSM2COMPUTER_HUB_PID" 2>/dev/null; then
    echo "not worth driving: pid $GSM2COMPUTER_HUB_PID is not running" >&2
    exit 1
  fi
  if ! assert_hub_pid "$GSM2COMPUTER_HUB_PID"; then
    echo "not worth driving: recorded pid is not hub.py" >&2
    exit 1
  fi
  GSM2COMPUTER_HUB_BASE="$GSM2COMPUTER_HUB_BASE" \
    GSM2COMPUTER_HUB_PID="$GSM2COMPUTER_HUB_PID" \
    python3 - <<'PY'
import json, os, sys, urllib.request

base = os.environ["GSM2COMPUTER_HUB_BASE"]

def get(path):
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.status, resp.read()

problems = []
try:
    status, raw = get("/health")
except Exception as exc:
    print(f"not worth driving: GET /health failed: {exc}")
    sys.exit(1)
try:
    health = json.loads(raw)
except json.JSONDecodeError:
    print("not worth driving: /health is not JSON")
    print(raw.decode("utf-8", "replace"))
    sys.exit(1)
if status != 200:
    problems.append(f"/health status {status}")
if health.get("ok") is not True:
    problems.append("health.ok is not true")
if health.get("openclaw_talk") != "off":
    problems.append(
        "openclaw_talk is %r; this stand-in sets GSM2COMPUTER_OPENCLAW_TALK=off"
        % (health.get("openclaw_talk"),)
    )
call = health.get("call") or {}
if call.get("busy") is not False:
    problems.append("call.busy is %r; slot is not idle" % (call.get("busy"),))
try:
    tstatus, traw = get("/portal/api/threads")
    threads = json.loads(traw)
except Exception as exc:
    problems.append(f"GET /portal/api/threads failed: {exc}")
    threads = None
else:
    if tstatus != 200 or not isinstance(threads, list):
        problems.append("/portal/api/threads is not a JSON list")
if problems:
    print("not worth driving:")
    for item in problems:
        print(" -", item)
    sys.exit(1)
print(
    "worth driving %s threads=%d talk=off call.busy=false pid=%s"
    % (base, len(threads), os.environ.get("GSM2COMPUTER_HUB_PID", ""))
)
PY
}

stop() {
  local home pid
  home=$(resolve_home)
  load_instance "$home"
  pid=${GSM2COMPUTER_HUB_PID:-}
  if [[ -z "$pid" ]]; then
    echo "no pid recorded in $home/instance.env" >&2
    exit 1
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "pid $pid is not running; left $home in place"
    return 0
  fi
  if ! assert_hub_pid "$pid"; then
    echo "refusing to kill pid $pid" >&2
    exit 1
  fi
  kill "$pid"
  local i
  for i in $(seq 1 25); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "stopped pid $pid; left $home in place"
      return 0
    fi
    sleep 0.2
  done
  kill -9 "$pid" 2>/dev/null || true
  echo "killed -9 pid $pid; left $home in place"
}

cmd=${1:-}
case "$cmd" in
  launch) launch ;;
  doctor) doctor ;;
  stop) stop ;;
  *) usage ;;
esac
