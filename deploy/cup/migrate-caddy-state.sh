#!/usr/bin/env bash
# Move Caddy's ACME storage from /caddy to /var/lib/caddy/caddy, or put it back.
#
#   migrate-caddy-state.sh apply
#   migrate-caddy-state.sh rollback
#
# apply refuses to move when the destination exists and is not an empty
# directory, so `mv` cannot nest the tree. It keeps the previous unit file,
# starts the new unit, and compares the certificate fingerprint. A mismatch
# stops Caddy immediately and runs rollback. A second apply, once the new
# layout is in place, does nothing.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
OLD=${GSM2COMPUTER_CADDY_STATE_OLD:-/caddy}
NEW=${GSM2COMPUTER_CADDY_STATE_NEW:-/var/lib/caddy/caddy}
UNIT_NAME=${GSM2COMPUTER_CADDY_UNIT:-gsm2computer-caddy.service}
UNIT_DEST=${GSM2COMPUTER_CADDY_UNIT_DEST:-/etc/systemd/system/$UNIT_NAME}
UNIT_SRC=${GSM2COMPUTER_CADDY_UNIT_SRC:-$SCRIPT_DIR/gsm2computer-caddy.service}
UNIT_BACKUP=${GSM2COMPUTER_CADDY_UNIT_BACKUP:-/var/lib/caddy/gsm2computer-caddy.service.bak}
PRE_SRC=${GSM2COMPUTER_CADDY_PRE_SRC:-$SCRIPT_DIR/gsm2computer-caddy-pre.sh}
PRE_DEST=${GSM2COMPUTER_CADDY_PRE_DEST:-/usr/local/sbin/gsm2computer-caddy-pre.sh}
BEFORE=${GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE:-/tmp/gsm-cert.before}
AFTER=${GSM2COMPUTER_CADDY_FINGERPRINT_AFTER:-/tmp/gsm-cert.after}
CONNECT=${GSM2COMPUTER_CADDY_CONNECT:-62.83.35.197:443}
SERVERNAME=${GSM2COMPUTER_CADDY_SERVERNAME:-gsm.ta3leam.com}

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

save_before() {
  local parent
  parent=$(dirname "$BEFORE")
  install -d -m 0755 "$parent"
  read_fingerprint | tee "$BEFORE" >/dev/null
  [[ -s "$BEFORE" ]] || die "could not read the certificate fingerprint"
}

ensure_new_parent() {
  local parent
  parent=$(dirname "$NEW")
  if [[ ! -d "$parent" ]]; then
    install -d -m 0755 "$parent"
  fi
}

# mv of a directory onto an existing directory nests the tree inside it.
prepare_dest_absent() {
  local dest=$1
  if [[ -d "$dest" ]]; then
    if dir_empty "$dest"; then
      rmdir "$dest"
      return 0
    fi
    die "$dest already exists and is not empty; refusing to nest the certificate tree"
  fi
  if [[ -e "$dest" ]]; then
    die "$dest exists and is not a directory; refusing the move"
  fi
}

backup_unit_once() {
  local parent
  parent=$(dirname "$UNIT_BACKUP")
  install -d -m 0755 "$parent"
  if [[ -f "$UNIT_BACKUP" ]]; then
    return 0
  fi
  if [[ ! -f "$UNIT_DEST" ]]; then
    die "installed unit missing, so there is nothing to restore: $UNIT_DEST"
  fi
  cp -a "$UNIT_DEST" "$UNIT_BACKUP"
}

install_new_unit() {
  [[ -f "$UNIT_SRC" ]] || die "new unit missing: $UNIT_SRC"
  install -d -m 0755 "$(dirname "$UNIT_DEST")"
  cp -a "$UNIT_SRC" "$UNIT_DEST"
  if [[ -f "$PRE_SRC" ]]; then
    install -d -m 0755 "$(dirname "$PRE_DEST")"
    install -m 0755 "$PRE_SRC" "$PRE_DEST"
  fi
}

restore_unit() {
  [[ -f "$UNIT_BACKUP" ]] || die "no unit backup at $UNIT_BACKUP"
  cp -a "$UNIT_BACKUP" "$UNIT_DEST"
}

stop_unit() {
  systemctl stop "$UNIT_NAME"
}

fingerprints_match() {
  local parent
  parent=$(dirname "$AFTER")
  install -d -m 0755 "$parent"
  read_fingerprint | tee "$AFTER" >/dev/null
  cmp -s "$BEFORE" "$AFTER"
}

already_applied() {
  [[ ! -e "$OLD" ]] && [[ -d "$NEW" ]] && ! dir_empty "$NEW" && unit_is_new
}

already_rolled_back() {
  [[ ! -e "$NEW" ]] && [[ -d "$OLD" ]] && ! dir_empty "$OLD" && [[ -f "$UNIT_DEST" ]] && ! unit_is_new
}

rollback_move() {
  if already_rolled_back; then
    echo "migrate-caddy-state: already at $OLD"
    return 0
  fi
  if [[ -e "$OLD" && -d "$NEW" ]] && ! dir_empty "$NEW" && ! dir_empty "$OLD"; then
    die "$OLD and $NEW both exist; refusing to nest the certificate tree"
  fi
  stop_unit || true
  if [[ -d "$NEW" ]]; then
    if [[ -d "$OLD" ]] && dir_empty "$OLD"; then
      rmdir "$OLD"
    fi
    if [[ -e "$OLD" ]]; then
      die "$OLD exists; refusing to nest the certificate tree"
    fi
    mv "$NEW" "$OLD"
  elif [[ ! -d "$OLD" ]]; then
    die "neither $NEW nor $OLD exists; nothing to roll back"
  fi
  restore_unit
  systemctl daemon-reload
  systemctl start "$UNIT_NAME"
  if ! fingerprints_match; then
    echo "migrate-caddy-state: fingerprint still differs after rollback; stopping Caddy" >&2
    stop_unit || true
    die "rollback served a different certificate; Caddy is stopped"
  fi
  echo "migrate-caddy-state: restored $OLD"
}

apply_move() {
  if already_applied; then
    echo "migrate-caddy-state: already at $NEW"
    return 0
  fi
  if [[ ! -e "$OLD" ]]; then
    die "missing $OLD and the new layout is not in place"
  fi
  if [[ -d "$NEW" ]] && ! dir_empty "$NEW"; then
    die "$NEW already exists and is not empty; refusing to nest the certificate tree"
  fi
  if [[ -e "$NEW" && ! -d "$NEW" ]]; then
    die "$NEW exists and is not a directory; refusing the move"
  fi
  save_before
  stop_unit
  ensure_new_parent
  backup_unit_once
  if [[ -d "$NEW" ]] && dir_empty "$NEW"; then
    rmdir "$NEW"
  fi
  prepare_dest_absent "$NEW"
  mv "$OLD" "$NEW"
  install_new_unit
  systemctl daemon-reload
  systemctl enable --now "$UNIT_NAME"
  if ! fingerprints_match; then
    echo "migrate-caddy-state: fingerprint changed; stopping Caddy" >&2
    stop_unit || true
    echo "migrate-caddy-state: rolling back" >&2
    rollback_move || true
    die "fingerprint changed; rolled back to $OLD"
  fi
  echo "migrate-caddy-state: applied, certificate unchanged"
}

usage() {
  echo "usage: deploy/cup/migrate-caddy-state.sh apply|rollback" >&2
  exit 2
}

cmd=${1:-}
case "$cmd" in
  apply) apply_move ;;
  rollback)
    if [[ ! -s "$BEFORE" ]]; then
      save_before
    fi
    rollback_move
    ;;
  *) usage ;;
esac
