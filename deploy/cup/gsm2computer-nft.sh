#!/usr/bin/env bash
# Idempotent nftables apply for cup's public interface, with a rollback timer.
#
#   gsm2computer-nft.sh check     # render + nft -c, do not touch the kernel
#   gsm2computer-nft.sh apply     # install the table, arm rollback
#   gsm2computer-nft.sh confirm   # keep the rules and load them on boot
#   gsm2computer-nft.sh revert    # restore the pre-apply table (or remove it)
#   gsm2computer-nft.sh status
#
# apply refuses to run unless a rollback timer can be armed. The default timer
# is systemd-run so it still fires if this SSH session dies. Sleep mode is only
# for a network namespace test and must be opted into.
set -euo pipefail

SCRIPT_PATH=$(readlink -f "$0")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
TEMPLATE=${GSM2COMPUTER_NFT_TEMPLATE:-$SCRIPT_DIR/gsm2computer.nft.in}
STATE_DIR=${GSM2COMPUTER_NFT_STATE:-/run/gsm2computer-nft}
PERSIST_PATH=${GSM2COMPUTER_NFT_PERSIST:-/etc/nftables.d/gsm2computer.nft}
UNIT_NAME=${GSM2COMPUTER_NFT_UNIT:-gsm2computer-nft.service}
ROLLBACK_UNIT=${GSM2COMPUTER_NFT_ROLLBACK_UNIT:-gsm2computer-nft-rollback.service}
ROLLBACK_S=${GSM2COMPUTER_NFT_ROLLBACK_S:-180}
ROLLBACK_MODE=${GSM2COMPUTER_NFT_ROLLBACK_MODE:-systemd}
PUBLIC_IP=${GSM2COMPUTER_PUBLIC_IP:-62.83.35.197}
TLS_PORT=${GSM2COMPUTER_PUBLIC_TLS_PORT:-443}
TS_PORT=${GSM2COMPUTER_TAILSCALE_UDP_PORT:-41641}
SSH_PORT=${GSM2COMPUTER_SSH_PORT:-22}
TABLE_FAMILY=inet
TABLE_NAME=gsm2computer

die() { echo "gsm2computer-nft: $*" >&2; exit 1; }

need_nft() { command -v nft >/dev/null 2>&1 || die "nft is not installed"; }

valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] || die "port is not a number: $1"
  (( "$1" >= 1 && "$1" <= 65535 )) || die "port out of range: $1"
}

detect_iface() {
  if [[ -n "${GSM2COMPUTER_PUBLIC_IF:-}" ]]; then
    printf '%s\n' "$GSM2COMPUTER_PUBLIC_IF"
    return
  fi
  ip -o -4 addr show | awk -v ip="$PUBLIC_IP" '$4 ~ "^"ip"/" { print $2; exit }'
}

validate_iface() {
  local iface=$1
  [[ "$iface" =~ ^[A-Za-z0-9._:-]+$ ]] || die "refusing unsafe interface name: $iface"
  [[ "$iface" != "tailscale0" && "$iface" != "lo" ]] || die "public interface cannot be $iface"
}

render_to() {
  local dest=$1
  local iface
  iface=$(detect_iface)
  [[ -n "$iface" ]] || die "no interface has $PUBLIC_IP; set GSM2COMPUTER_PUBLIC_IF"
  validate_iface "$iface"
  valid_port "$TLS_PORT"
  valid_port "$TS_PORT"
  valid_port "$SSH_PORT"
  [[ -f "$TEMPLATE" ]] || die "missing template $TEMPLATE"
  sed \
    -e "s/__PUBLIC_IF__/${iface}/g" \
    -e "s/__TLS_PORT__/${TLS_PORT}/g" \
    -e "s/__TS_PORT__/${TS_PORT}/g" \
    -e "s/__SSH_PORT__/${SSH_PORT}/g" \
    "$TEMPLATE" >"$dest"
  if grep -q 'flush ruleset' "$dest"; then
    die "rendered ruleset contains flush ruleset; refusing"
  fi
  printf '%s\n' "$iface"
}

table_exists() {
  nft list table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1
}

save_baseline_if_needed() {
  mkdir -p "$STATE_DIR"
  if [[ -f "$STATE_DIR/rollback-armed" ]]; then
    return
  fi
  if table_exists; then
    nft list table "$TABLE_FAMILY" "$TABLE_NAME" >"$STATE_DIR/baseline.nft"
    echo table >"$STATE_DIR/baseline.mode"
  else
    rm -f "$STATE_DIR/baseline.nft"
    echo absent >"$STATE_DIR/baseline.mode"
  fi
}

restore_baseline() {
  nft delete table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1 || true
  local mode=absent
  if [[ -f "$STATE_DIR/baseline.mode" ]]; then
    mode=$(cat "$STATE_DIR/baseline.mode")
  fi
  if [[ "$mode" == "table" && -f "$STATE_DIR/baseline.nft" ]]; then
    nft -f "$STATE_DIR/baseline.nft"
    echo "restored previous $TABLE_FAMILY $TABLE_NAME table"
  else
    echo "removed $TABLE_FAMILY $TABLE_NAME table"
  fi
  rm -f "$STATE_DIR/rollback-armed"
}

stop_timer_process() {
  if [[ -f "$STATE_DIR/rollback.pid" ]]; then
    local pid
    pid=$(cat "$STATE_DIR/rollback.pid" || true)
    if [[ -n "${pid:-}" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$STATE_DIR/rollback.pid"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop "$ROLLBACK_UNIT" >/dev/null 2>&1 || true
    local timer=${ROLLBACK_UNIT%.service}.timer
    systemctl stop "$timer" >/dev/null 2>&1 || true
  fi
}

disarm_timer() {
  stop_timer_process
  rm -f "$STATE_DIR/rollback-armed"
}

arm_timer() {
  mkdir -p "$STATE_DIR"
  # A second apply must not leave the previous timer armed. That timer still
  # points at the original baseline and would revert the newer rules.
  stop_timer_process
  echo 1 >"$STATE_DIR/rollback-armed"
  case "$ROLLBACK_MODE" in
    systemd)
      command -v systemd-run >/dev/null 2>&1 || die "systemd-run is required to arm rollback"
      systemd-run --unit="$ROLLBACK_UNIT" --on-active="${ROLLBACK_S}s" --collect \
        /usr/bin/bash "$SCRIPT_PATH" rollback-now \
        || die "could not arm systemd rollback timer"
      ;;
    sleep)
      [[ "${GSM2COMPUTER_NFT_ALLOW_SLEEP:-}" == "1" ]] \
        || die "sleep rollback is only for a netns test; set GSM2COMPUTER_NFT_ALLOW_SLEEP=1"
      # Own stdout, and kill the sleeper on SIGTERM. Otherwise the sleep
      # inherits the caller's pipe and outlives the shell that started it.
      (
        trap 'kill "$sleep_pid" 2>/dev/null || true' EXIT
        sleep "$ROLLBACK_S" &
        sleep_pid=$!
        wait "$sleep_pid" || true
        if [[ -f "$STATE_DIR/rollback-armed" ]]; then
          /usr/bin/bash "$SCRIPT_PATH" rollback-now
        fi
      ) >"$STATE_DIR/rollback.log" 2>&1 &
      echo $! >"$STATE_DIR/rollback.pid"
      ;;
    *)
      die "unknown rollback mode: $ROLLBACK_MODE"
      ;;
  esac
}

cmd=${1:-}
case "$cmd" in
  render)
    need_nft
    rendered=$(mktemp)
    iface=$(render_to "$rendered")
    cat "$rendered"
    rm -f "$rendered"
    echo "rendered for $iface" >&2
    ;;
  check)
    need_nft
    rendered=$(mktemp)
    iface=$(render_to "$rendered")
    nft -c -f "$rendered"
    rm -f "$rendered"
    echo "nft check ok for $iface (not applied)"
    ;;
  apply)
    need_nft
    iface=$(detect_iface)
    [[ -n "$iface" ]] || die "no interface has $PUBLIC_IP; set GSM2COMPUTER_PUBLIC_IF"
    validate_iface "$iface"
    if [[ "${GSM2COMPUTER_NFT_SKIP_LINK_CHECK:-}" != "1" ]]; then
      ip link show dev "$iface" >/dev/null 2>&1 || die "interface $iface does not exist"
    fi
    mkdir -p "$STATE_DIR"
    rendered=$STATE_DIR/pending.nft
    render_to "$rendered" >/dev/null
    nft -c -f "$rendered"
    save_baseline_if_needed
    # Arm first so a ruleset that wedges this shell still reverts.
    arm_timer
    if ! nft delete table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1; then
      true
    fi
    if ! nft -f "$rendered"; then
      echo "apply failed; restoring baseline" >&2
      restore_baseline || true
      disarm_timer || true
      exit 1
    fi
    echo "applied $TABLE_FAMILY $TABLE_NAME on $iface (ssh $SSH_PORT, tls $TLS_PORT, tailscale udp $TS_PORT)"
    echo "existing SSH stays up via conntrack. Open a SECOND session and confirm it can log in."
    echo "rollback in ${ROLLBACK_S}s unless you run: $SCRIPT_PATH confirm"
    echo "immediate undo: $SCRIPT_PATH revert"
    ;;
  confirm)
    [[ -f "$STATE_DIR/pending.nft" ]] || die "nothing to confirm; run apply first"
    disarm_timer
    rm -f "$STATE_DIR/baseline.nft" "$STATE_DIR/baseline.mode"
    install -d -m 0755 "$(dirname "$PERSIST_PATH")"
    install -m 0644 "$STATE_DIR/pending.nft" "$PERSIST_PATH"
    install -d -m 0755 /usr/local/sbin
    install -m 0755 "$SCRIPT_PATH" /usr/local/sbin/gsm2computer-nft.sh
    install -m 0644 "$SCRIPT_DIR/gsm2computer-nft.service" "/etc/systemd/system/$UNIT_NAME"
    systemctl daemon-reload
    systemctl enable "$UNIT_NAME"
    echo "confirmed. $UNIT_NAME will load $PERSIST_PATH on boot. Rollback timer cancelled."
    ;;
  rollback-now)
    need_nft
    restore_baseline
    ;;
  revert)
    need_nft
    disarm_timer
    restore_baseline
    if [[ -f "/etc/systemd/system/$UNIT_NAME" ]]; then
      systemctl disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
    fi
    rm -f "$PERSIST_PATH"
    echo "reverted. Boot unit disabled and $PERSIST_PATH removed."
    ;;
  boot)
    need_nft
    [[ -f "$PERSIST_PATH" ]] || die "no persisted ruleset at $PERSIST_PATH"
    if grep -q 'flush ruleset' "$PERSIST_PATH"; then
      die "persisted ruleset contains flush ruleset; refusing"
    fi
    nft delete table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1 || true
    nft -f "$PERSIST_PATH"
    ;;
  status)
    need_nft
    if table_exists; then
      nft list table "$TABLE_FAMILY" "$TABLE_NAME"
    else
      echo "table $TABLE_FAMILY $TABLE_NAME is not loaded"
    fi
    if [[ -f "$STATE_DIR/rollback-armed" ]]; then
      echo "rollback timer: armed (${ROLLBACK_S}s mode, mode=$ROLLBACK_MODE)"
    else
      echo "rollback timer: not armed"
    fi
    ;;
  *)
    echo "usage: $0 {check|apply|confirm|revert|status|boot|render}" >&2
    exit 2
    ;;
esac
