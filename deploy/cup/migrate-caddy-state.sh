#!/usr/bin/env bash
# Copy Caddy's ACME storage from /caddy to /var/lib/caddy/caddy and switch the
# unit. /caddy is left in place. Rollback only puts the previous unit back.
#
#   migrate-caddy-state.sh apply
#   migrate-caddy-state.sh rollback
#
# apply refuses, without stopping Caddy, when the destination already has
# different files. The fingerprint and the unit backup are stored under
# /var/lib/caddy. Any failure after the stop restores that unit explicitly.
# systemctl start returns before the TLS listener answers, so the post-start
# fingerprint read retries until a certificate is printed.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
OLD=${GSM2COMPUTER_CADDY_STATE_OLD:-/caddy}
NEW=${GSM2COMPUTER_CADDY_STATE_NEW:-/var/lib/caddy/caddy}
UNIT_NAME=${GSM2COMPUTER_CADDY_UNIT:-gsm2computer-caddy.service}
UNIT_DEST=${GSM2COMPUTER_CADDY_UNIT_DEST:-/etc/systemd/system/$UNIT_NAME}
UNIT_SRC=${GSM2COMPUTER_CADDY_UNIT_SRC:-$SCRIPT_DIR/gsm2computer-caddy.service}
STATE_PARENT=$(dirname "$NEW")
UNIT_BACKUP=${GSM2COMPUTER_CADDY_UNIT_BACKUP:-$STATE_PARENT/gsm2computer-caddy.service.bak}
PRE_SRC=${GSM2COMPUTER_CADDY_PRE_SRC:-$SCRIPT_DIR/gsm2computer-caddy-pre.sh}
PRE_DEST=${GSM2COMPUTER_CADDY_PRE_DEST:-/usr/local/sbin/gsm2computer-caddy-pre.sh}
BEFORE=${GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE:-$STATE_PARENT/gsm-cert.before}
AFTER=${GSM2COMPUTER_CADDY_FINGERPRINT_AFTER:-$STATE_PARENT/gsm-cert.after}
CONNECT=${GSM2COMPUTER_CADDY_CONNECT:-62.83.35.197:443}
SERVERNAME=${GSM2COMPUTER_CADDY_SERVERNAME:-gsm.ta3leam.com}
# Backoff after start: 1, 2, 4, then 8 seconds, and never past this cap.
FINGERPRINT_WAIT_SECS=${GSM2COMPUTER_CADDY_FINGERPRINT_WAIT_SECS:-30}

die() {
  echo "migrate-caddy-state: $*" >&2
  exit 1
}

dir_empty() {
  [[ -d "$1" ]] && [[ -z $(find "$1" -mindepth 1 -print -quit) ]]
}

unit_is_new() {
  [[ -f "$UNIT_DEST" ]] && grep -q 'XDG_DATA_HOME=/var/lib/caddy' "$UNIT_DEST"
}

read_fingerprint() {
  if [[ -n "${GSM2COMPUTER_CADDY_FINGERPRINT_CMD:-}" ]]; then
    "$GSM2COMPUTER_CADDY_FINGERPRINT_CMD"
    return
  fi
  echo | openssl s_client -connect "$CONNECT" -servername "$SERVERNAME" 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256
}

# Used only after start. Empty and failed reads mean TLS is not up yet, so
# they are not compared. Prints the fingerprint once TLS answers.
read_fingerprint_after_start() {
  local delay=1 waited=0 out rc
  while true; do
    out=""
    rc=0
    # Keep trailing newlines so the later cmp matches the saved file.
    out=$(read_fingerprint && printf x) || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      out=${out%x}
      if [[ -n "${out//[[:space:]]/}" ]]; then
        printf '%s' "$out"
        return 0
      fi
    fi
    if (( waited >= FINGERPRINT_WAIT_SECS )); then
      return 1
    fi
    if (( waited + delay > FINGERPRINT_WAIT_SECS )); then
      delay=$((FINGERPRINT_WAIT_SECS - waited))
    fi
    sleep "$delay"
    waited=$((waited + delay))
    if (( delay < 8 )); then
      delay=$((delay * 2))
    fi
  done
}

# Non-empty destination whose files are not the source tree.
destination_conflicts() {
  if [[ ! -e "$NEW" ]]; then
    return 1
  fi
  if [[ ! -d "$NEW" ]]; then
    return 0
  fi
  if dir_empty "$NEW"; then
    return 1
  fi
  ! diff -rq "$OLD" "$NEW" >/dev/null 2>&1
}

save_fingerprint() {
  install -d -m 0755 "$(dirname "$BEFORE")"
  read_fingerprint >"$BEFORE"
  [[ -s "$BEFORE" ]] || die "could not read the certificate fingerprint"
}

backup_unit_once() {
  install -d -m 0755 "$(dirname "$UNIT_BACKUP")"
  if [[ -f "$UNIT_BACKUP" ]]; then
    return 0
  fi
  if [[ ! -f "$UNIT_DEST" ]]; then
    die "installed unit missing, so there is nothing to restore: $UNIT_DEST"
  fi
  cp -a "$UNIT_DEST" "$UNIT_BACKUP"
}

install_new_unit() {
  [[ -f "$UNIT_SRC" ]] || return 1
  install -d -m 0755 "$(dirname "$UNIT_DEST")"
  cp -a "$UNIT_SRC" "$UNIT_DEST"
  if [[ -f "$PRE_SRC" ]]; then
    install -d -m 0755 "$(dirname "$PRE_DEST")"
    install -m 0755 "$PRE_SRC" "$PRE_DEST"
  fi
}

copy_state() {
  install -d -m 0755 "$NEW"
  cp -a "$OLD"/. "$NEW"/
}

# Explicit recovery. set -e must not be what stops Caddy or puts the unit back.
restore_old_unit() {
  local why=$1
  echo "migrate-caddy-state: $why; stopping and restoring the previous unit" >&2
  systemctl stop "$UNIT_NAME" || true
  if [[ ! -f "$UNIT_BACKUP" ]]; then
    echo "migrate-caddy-state: no unit backup at $UNIT_BACKUP" >&2
    exit 1
  fi
  cp -a "$UNIT_BACKUP" "$UNIT_DEST" || exit 1
  systemctl daemon-reload || true
  systemctl start "$UNIT_NAME" || true
  exit 1
}

# 0 match, 1 TLS answered with a different certificate, 2 TLS stayed silent.
fingerprints_match() {
  install -d -m 0755 "$(dirname "$AFTER")"
  if ! read_fingerprint_after_start >"$AFTER"; then
    return 2
  fi
  if cmp -s "$BEFORE" "$AFTER"; then
    return 0
  fi
  return 1
}

apply_copy() {
  [[ -d "$OLD" ]] || die "missing $OLD"
  if destination_conflicts; then
    die "$NEW is non-empty and differs from $OLD; Caddy is still running the current unit"
  fi
  if unit_is_new && [[ -d "$NEW" ]] && ! dir_empty "$NEW"; then
    echo "migrate-caddy-state: already copied to $NEW"
    return 0
  fi
  save_fingerprint
  backup_unit_once
  systemctl stop "$UNIT_NAME" || die "could not stop $UNIT_NAME; left the current unit in place"
  if ! copy_state; then
    restore_old_unit "copy into $NEW failed"
  fi
  if ! install_new_unit; then
    restore_old_unit "could not install the new unit"
  fi
  if ! systemctl daemon-reload; then
    restore_old_unit "daemon-reload failed"
  fi
  if ! systemctl start "$UNIT_NAME"; then
    restore_old_unit "start failed"
  fi
  local match=0
  fingerprints_match || match=$?
  if [[ "$match" -eq 2 ]]; then
    restore_old_unit "certificate did not answer on TLS"
  fi
  if [[ "$match" -eq 1 ]]; then
    restore_old_unit "fingerprint changed"
  fi
  echo "migrate-caddy-state: applied, certificate unchanged; $OLD left in place"
}

rollback_unit() {
  systemctl stop "$UNIT_NAME" || true
  if [[ ! -f "$UNIT_BACKUP" ]]; then
    die "no unit backup at $UNIT_BACKUP"
  fi
  cp -a "$UNIT_BACKUP" "$UNIT_DEST" || die "could not restore $UNIT_BACKUP"
  systemctl daemon-reload || true
  systemctl start "$UNIT_NAME" || true
  if [[ ! -s "$BEFORE" ]]; then
    echo "migrate-caddy-state: no saved fingerprint; restored the previous unit"
    return 0
  fi
  local match=0
  fingerprints_match || match=$?
  if [[ "$match" -eq 2 ]]; then
    echo "migrate-caddy-state: certificate did not answer on TLS" >&2
    exit 1
  fi
  if [[ "$match" -eq 1 ]]; then
    echo "migrate-caddy-state: fingerprint differs from $BEFORE" >&2
    exit 1
  fi
  echo "migrate-caddy-state: restored the previous unit, certificate unchanged"
}

usage() {
  echo "usage: deploy/cup/migrate-caddy-state.sh apply|rollback" >&2
  exit 2
}

cmd=${1:-}
case "$cmd" in
  apply) apply_copy ;;
  rollback) rollback_unit ;;
  *) usage ;;
esac
