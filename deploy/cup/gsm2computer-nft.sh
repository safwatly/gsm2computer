#!/usr/bin/env bash
# Insert one rule into cup's existing `inet filter` input chain:
# TCP 443 on the public NIC. Does not flush, does not add a second table,
# and does not touch UDP 41641 or 41642.
#
#   gsm2computer-nft.sh check     # render + nft -c, do not touch the kernel
#   gsm2computer-nft.sh apply     # insert the live rule, arm rollback
#   gsm2computer-nft.sh confirm   # include the drop-in from nftables.conf
#   gsm2computer-nft.sh revert    # delete the rule and the include
#   gsm2computer-nft.sh status
#
# apply refuses to run unless a rollback timer can be armed. The default timer
# is systemd-run so it still fires if this SSH session dies. Sleep mode is only
# for a network namespace test and must be opted into.
set -euo pipefail

SCRIPT_PATH=$(readlink -f "$0")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
TEMPLATE=${GSM2COMPUTER_NFT_TEMPLATE:-$SCRIPT_DIR/gsm2computer-public.nft.in}
STATE_DIR=${GSM2COMPUTER_NFT_STATE:-/run/gsm2computer-nft}
PERSIST_PATH=${GSM2COMPUTER_NFT_PERSIST:-/etc/nftables.d/gsm2computer-public.nft}
CONF_PATH=${GSM2COMPUTER_NFT_CONF:-/etc/nftables.conf}
UNIT_NAME=${GSM2COMPUTER_NFT_UNIT:-gsm2computer-nft.service}
ROLLBACK_UNIT=${GSM2COMPUTER_NFT_ROLLBACK_UNIT:-gsm2computer-nft-rollback.service}
SBIN_DIR=${GSM2COMPUTER_NFT_SBIN:-/usr/local/sbin}
UNIT_DEST=${GSM2COMPUTER_NFT_UNIT_DEST:-/etc/systemd/system/$UNIT_NAME}
ROLLBACK_S=${GSM2COMPUTER_NFT_ROLLBACK_S:-180}
ROLLBACK_MODE=${GSM2COMPUTER_NFT_ROLLBACK_MODE:-systemd}
PUBLIC_IP=${GSM2COMPUTER_PUBLIC_IP:-62.83.35.197}
TLS_PORT=${GSM2COMPUTER_PUBLIC_TLS_PORT:-443}
SSH_PORT=${GSM2COMPUTER_SSH_PORT:-22}
TABLE_FAMILY=inet
TABLE_NAME=filter
COMMENT=gsm2computer-public-tls
BEGIN_MARK='# BEGIN gsm2computer-public-entrance'
END_MARK='# END gsm2computer-public-entrance'

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

input_chain() {
  nft -j list table "$TABLE_FAMILY" "$TABLE_NAME" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(2)
names = []
for item in data.get("nftables", []):
    chain = item.get("chain") or {}
    if chain.get("hook") == "input" and chain.get("name"):
        names.append(chain["name"])
if len(names) != 1:
    sys.stderr.write("input chains: %s\n" % ",".join(names))
    sys.exit(3)
print(names[0])
'
}

table_exists() {
  nft list table "$TABLE_FAMILY" "$TABLE_NAME" >/dev/null 2>&1
}

rule_handles() {
  local chain=$1
  nft -a list chain "$TABLE_FAMILY" "$TABLE_NAME" "$chain" 2>/dev/null \
    | awk -v comment="$COMMENT" 'index($0, comment) { print $NF }'
}

delete_our_rules() {
  local chain=$1 handle
  while read -r handle; do
    [[ -n "${handle:-}" ]] || continue
    nft delete rule "$TABLE_FAMILY" "$TABLE_NAME" "$chain" handle "$handle"
  done < <(rule_handles "$chain")
}

render_to() {
  local dest=$1 iface=$2 chain=$3
  [[ -n "$iface" ]] || die "no interface has $PUBLIC_IP; set GSM2COMPUTER_PUBLIC_IF"
  validate_iface "$iface"
  [[ "$chain" =~ ^[A-Za-z0-9._-]+$ ]] || die "refusing unsafe chain name: $chain"
  valid_port "$TLS_PORT"
  valid_port "$SSH_PORT"
  [[ -f "$TEMPLATE" ]] || die "missing template $TEMPLATE"
  sed \
    -e "s/__PUBLIC_IF__/${iface}/g" \
    -e "s/__TLS_PORT__/${TLS_PORT}/g" \
    -e "s/__INPUT_CHAIN__/${chain}/g" \
    "$TEMPLATE" >"$dest"
  if grep -v '^[[:space:]]*#' "$dest" | grep -q 'flush ruleset'; then
    die "rendered drop-in contains flush ruleset; refusing"
  fi
  if grep -v '^[[:space:]]*#' "$dest" | grep -q '^table '; then
    die "rendered drop-in declares a table; refusing"
  fi
}

include_block() {
  local path=$1
  printf '%s\ninclude "%s"\n%s\n' "$BEGIN_MARK" "$path" "$END_MARK"
}

strip_include() {
  local file=$1
  [[ -f "$file" ]] || return 0
  python3 - "$file" "$BEGIN_MARK" "$END_MARK" <<'PY'
import pathlib, sys
path, begin, end = sys.argv[1:]
file = pathlib.Path(path)
text = file.read_text()
start = text.find(begin)
stop = text.find(end)
if start == -1 or stop == -1 or stop < start:
    sys.exit(0)
stop = stop + len(end)
if stop < len(text) and text[stop] == "\n":
    stop += 1
file.write_text(text[:start] + text[stop:])
PY
}

conf_has_include() {
  [[ -f "$CONF_PATH" ]] && grep -q "$BEGIN_MARK" "$CONF_PATH"
}

append_include() {
  local file=$1 path=$2
  strip_include "$file"
  printf '\n%s\n' "$(include_block "$path")" >>"$file"
}

systemctl_cmd() {
  if [[ -n "${GSM2COMPUTER_NFT_SYSTEMCTL:-}" ]]; then
    "$GSM2COMPUTER_NFT_SYSTEMCTL" "$@"
  else
    systemctl "$@"
  fi
}

remember_chain() {
  local chain=$1
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$chain" >"$STATE_DIR/chain"
}

saved_chain() {
  if [[ -f "$STATE_DIR/chain" ]]; then
    cat "$STATE_DIR/chain"
    return
  fi
  input_chain
}

save_baseline_if_needed() {
  local chain=$1
  mkdir -p "$STATE_DIR"
  remember_chain "$chain"
  if [[ -f "$STATE_DIR/rollback-armed" ]]; then
    return
  fi
  if [[ -n "$(rule_handles "$chain")" ]]; then
    echo present >"$STATE_DIR/baseline.mode"
  else
    echo absent >"$STATE_DIR/baseline.mode"
  fi
}

restore_baseline() {
  local chain mode=absent
  chain=$(saved_chain) || die "cannot find inet filter input chain to roll back"
  if [[ -f "$STATE_DIR/baseline.mode" ]]; then
    mode=$(cat "$STATE_DIR/baseline.mode")
  fi
  if [[ "$mode" == "absent" ]]; then
    delete_our_rules "$chain"
    echo "removed $COMMENT from $TABLE_FAMILY $TABLE_NAME $chain"
  else
    echo "left the pre-existing $COMMENT rule in place"
  fi
  if [[ -f "$STATE_DIR/conf-edited" ]]; then
    strip_include "$CONF_PATH"
    rm -f "$STATE_DIR/conf-edited"
    echo "removed the gsm2computer include from $CONF_PATH"
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
  if [[ "$ROLLBACK_MODE" == "sleep" ]]; then
    return
  fi
  systemctl_cmd stop "$ROLLBACK_UNIT" >/dev/null 2>&1 || true
  local timer=${ROLLBACK_UNIT%.service}.timer
  systemctl_cmd stop "$timer" >/dev/null 2>&1 || true
}

disarm_timer() {
  stop_timer_process
  rm -f "$STATE_DIR/rollback-armed"
}

arm_timer() {
  mkdir -p "$STATE_DIR"
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

syntax_check_fragment() {
  local fragment=$1 iface chain wrapper
  iface=$(detect_iface)
  chain=${2:-input}
  wrapper=$(mktemp)
  cat >"$wrapper" <<EOF
table inet filter {
	chain ${chain} {
		type filter hook input priority filter; policy drop;
		tcp dport ${SSH_PORT} accept comment "ssh"
		udp dport 41641 accept comment "tailscale"
		udp dport 41642 accept comment "tailscale-portal"
	}
}
EOF
  cat "$fragment" >>"$wrapper"
  if grep -v '^[[:space:]]*#' "$fragment" | grep -q 'flush ruleset'; then
    rm -f "$wrapper"
    die "drop-in contains flush ruleset; refusing"
  fi
  nft -c -f "$wrapper"
  rm -f "$wrapper"
  printf '%s\n' "$iface"
}

cmd=${1:-}
case "$cmd" in
  render)
    need_nft
    iface=$(detect_iface)
    chain=${GSM2COMPUTER_NFT_CHAIN:-input}
    rendered=$(mktemp)
    render_to "$rendered" "$iface" "$chain"
    cat "$rendered"
    rm -f "$rendered"
    echo "rendered for $iface chain $chain" >&2
    ;;
  check)
    need_nft
    iface=$(detect_iface)
    [[ -n "$iface" ]] || die "no interface has $PUBLIC_IP; set GSM2COMPUTER_PUBLIC_IF"
    rendered=$(mktemp)
    render_to "$rendered" "$iface" "${GSM2COMPUTER_NFT_CHAIN:-input}"
    syntax_check_fragment "$rendered" "${GSM2COMPUTER_NFT_CHAIN:-input}" >/dev/null
    rm -f "$rendered"
    echo "nft check ok for $iface (drop-in only, not applied)"
    ;;
  apply)
    need_nft
    iface=$(detect_iface)
    [[ -n "$iface" ]] || die "no interface has $PUBLIC_IP; set GSM2COMPUTER_PUBLIC_IF"
    validate_iface "$iface"
    if [[ "${GSM2COMPUTER_NFT_SKIP_LINK_CHECK:-}" != "1" ]]; then
      ip link show dev "$iface" >/dev/null 2>&1 || die "interface $iface does not exist"
    fi
    table_exists || die "table inet filter is not loaded; refusing to create a second table"
    chain=$(input_chain) || die "table inet filter has no single input hook; refusing"
    mkdir -p "$STATE_DIR"
    rendered=$STATE_DIR/pending.nft
    render_to "$rendered" "$iface" "$chain"
    nft -c -f "$rendered"
    save_baseline_if_needed "$chain"
    arm_timer
    if [[ -z "$(rule_handles "$chain")" ]]; then
      if ! nft -f "$rendered"; then
        echo "apply failed; restoring baseline" >&2
        restore_baseline || true
        disarm_timer || true
        exit 1
      fi
    fi
    echo "inserted $COMMENT on $iface chain $chain (tcp $TLS_PORT). Host filter unchanged aside from that rule."
    echo "UDP 41641 and 41642 were not modified."
    echo "existing SSH stays up. Open a SECOND session and confirm it can log in."
    echo "rollback in ${ROLLBACK_S}s unless you run: $SCRIPT_PATH confirm"
    echo "immediate undo: $SCRIPT_PATH revert"
    ;;
  confirm)
    [[ -f "$STATE_DIR/pending.nft" ]] || die "nothing to confirm; run apply first"
    [[ -f "$CONF_PATH" ]] || die "no host ruleset at $CONF_PATH"
    # Check a copy before touching the host file. The copy includes flush if
    # the host file does; nft -c does not apply it.
    check_conf=$(mktemp)
    check_frag=$(mktemp)
    cp "$STATE_DIR/pending.nft" "$check_frag"
    cp "$CONF_PATH" "$check_conf"
    strip_include "$check_conf"
    printf '\n%s\n' "$(include_block "$check_frag")" >>"$check_conf"
    if ! nft -c -f "$check_conf"; then
      rm -f "$check_conf" "$check_frag"
      die "nft -c rejected $CONF_PATH with the drop-in; host file not changed"
    fi
    rm -f "$check_conf" "$check_frag"
    install -d -m 0755 "$(dirname "$PERSIST_PATH")"
    install -m 0644 "$STATE_DIR/pending.nft" "$PERSIST_PATH"
    if grep -v '^[[:space:]]*#' "$PERSIST_PATH" | grep -q 'flush ruleset'; then
      die "persisted drop-in contains flush ruleset; refusing"
    fi
    append_include "$CONF_PATH" "$PERSIST_PATH"
    echo 1 >"$STATE_DIR/conf-edited"
    if ! nft -c -f "$CONF_PATH"; then
      strip_include "$CONF_PATH"
      rm -f "$STATE_DIR/conf-edited" "$PERSIST_PATH"
      die "nft -c rejected the updated $CONF_PATH; include removed"
    fi
    install -d -m 0755 "$SBIN_DIR"
    install -m 0755 "$SCRIPT_PATH" "$SBIN_DIR/gsm2computer-nft.sh"
    install -d -m 0755 "$(dirname "$UNIT_DEST")"
    install -m 0644 "$SCRIPT_DIR/gsm2computer-nft.service" "$UNIT_DEST"
    if ! systemctl_cmd daemon-reload; then
      strip_include "$CONF_PATH"
      rm -f "$STATE_DIR/conf-edited"
      die "systemctl daemon-reload failed; include removed, rollback still armed"
    fi
    if ! systemctl_cmd enable "$UNIT_NAME"; then
      strip_include "$CONF_PATH"
      rm -f "$STATE_DIR/conf-edited"
      die "systemctl enable failed; include removed, rollback still armed"
    fi
    disarm_timer
    rm -f "$STATE_DIR/baseline.nft" "$STATE_DIR/baseline.mode" "$STATE_DIR/conf-edited"
    echo "confirmed. $CONF_PATH includes $PERSIST_PATH. $UNIT_NAME re-inserts the rule after nftables.service. Rollback timer cancelled."
    ;;
  rollback-now)
    need_nft
    restore_baseline
    ;;
  revert)
    need_nft
    disarm_timer
    if table_exists; then
      chain=$(saved_chain) || chain=$(input_chain) || chain=""
      if [[ -n "$chain" ]]; then
        delete_our_rules "$chain"
      fi
    fi
    strip_include "$CONF_PATH"
    rm -f "$STATE_DIR/conf-edited" "$STATE_DIR/baseline.mode" "$STATE_DIR/chain"
    if [[ -f "$UNIT_DEST" ]]; then
      systemctl_cmd disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
    fi
    rm -f "$PERSIST_PATH"
    echo "reverted. TCP 443 drop-in removed. Host filter and UDP 41642 were not flushed."
    ;;
  boot)
    need_nft
    [[ -f "$PERSIST_PATH" ]] || die "no persisted drop-in at $PERSIST_PATH"
    if grep -v '^[[:space:]]*#' "$PERSIST_PATH" | grep -q 'flush ruleset'; then
      die "persisted drop-in contains flush ruleset; refusing"
    fi
    table_exists || die "table inet filter is not loaded; boot will not create one"
    chain=$(input_chain) || die "table inet filter has no single input hook"
    if [[ -n "$(rule_handles "$chain")" ]]; then
      echo "$COMMENT already present"
      exit 0
    fi
    nft -f "$PERSIST_PATH"
    ;;
  status)
    need_nft
    if table_exists; then
      chain=$(input_chain) || chain=""
      if [[ -n "$chain" ]]; then
        echo "inet filter input chain: $chain"
        nft -a list chain inet filter "$chain" | awk -v comment="$COMMENT" 'index($0, comment) || /41642/ || /dport 22 /'
      fi
    else
      echo "table inet filter is not loaded"
    fi
    if [[ -f "$STATE_DIR/rollback-armed" ]]; then
      echo "rollback timer: armed (${ROLLBACK_S}s, mode=$ROLLBACK_MODE)"
    else
      echo "rollback timer: not armed"
    fi
    if conf_has_include; then
      echo "include: present in $CONF_PATH"
    else
      echo "include: not in $CONF_PATH"
    fi
    ;;
  *)
    echo "usage: $0 {check|apply|confirm|revert|status|boot|render}" >&2
    exit 2
    ;;
esac
