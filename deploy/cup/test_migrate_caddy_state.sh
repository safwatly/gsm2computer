#!/usr/bin/env bash
# Prove the Caddy state move will not nest the certificate tree, and that a
# changed fingerprint stops Caddy and puts the old directory and unit back.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
MIGRATE=$ROOT/deploy/cup/migrate-caddy-state.sh
EVIDENCE=${GSM2COMPUTER_VERIFY_EVIDENCE:-/opt/cursor/artifacts/verify-gsm2computer}
mkdir -p "$EVIDENCE"
LOG=$EVIDENCE/caddy-migrate.txt
: >"$LOG"

fail() {
  echo "FAIL: $*" | tee -a "$LOG" >&2
  exit 1
}

note() {
  echo "$*" | tee -a "$LOG"
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
BIN=$WORK/bin
mkdir -p "$BIN"

cat >"$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$GSM2COMPUTER_CADDY_ACTION_LOG"
if [[ "${1:-}" == "enable" && -f "${GSM2COMPUTER_CADDY_FINGERPRINT_ON_ENABLE:-}" ]]; then
  cp "$GSM2COMPUTER_CADDY_FINGERPRINT_ON_ENABLE" "$GSM2COMPUTER_CADDY_FINGERPRINT_NOW"
fi
if [[ "${1:-}" == "start" && -f "${GSM2COMPUTER_CADDY_FINGERPRINT_ON_START:-}" ]]; then
  cp "$GSM2COMPUTER_CADDY_FINGERPRINT_ON_START" "$GSM2COMPUTER_CADDY_FINGERPRINT_NOW"
fi
exit 0
EOF
cat >"$BIN/fingerprint" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cat "$GSM2COMPUTER_CADDY_FINGERPRINT_NOW"
EOF
chmod 755 "$BIN/systemctl" "$BIN/fingerprint"

export PATH="$BIN:/usr/bin:/bin"
export GSM2COMPUTER_CADDY_STATE_OLD=$WORK/caddy
export GSM2COMPUTER_CADDY_STATE_NEW=$WORK/var/lib/caddy/caddy
export GSM2COMPUTER_CADDY_UNIT_DEST=$WORK/systemd/gsm2computer-caddy.service
export GSM2COMPUTER_CADDY_UNIT_SRC=$ROOT/deploy/cup/gsm2computer-caddy.service
export GSM2COMPUTER_CADDY_UNIT_BACKUP=$WORK/var/lib/caddy/gsm2computer-caddy.service.bak
export GSM2COMPUTER_CADDY_PRE_SRC=$ROOT/deploy/cup/gsm2computer-caddy-pre.sh
export GSM2COMPUTER_CADDY_PRE_DEST=$WORK/sbin/gsm2computer-caddy-pre.sh
export GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE=$WORK/gsm-cert.before
export GSM2COMPUTER_CADDY_FINGERPRINT_AFTER=$WORK/gsm-cert.after
export GSM2COMPUTER_CADDY_FINGERPRINT_CMD=$BIN/fingerprint
export GSM2COMPUTER_CADDY_FINGERPRINT_NOW=$WORK/fingerprint
export GSM2COMPUTER_CADDY_ACTION_LOG=$WORK/actions

reset_tree() {
  rm -rf "$WORK/caddy" "$WORK/var" "$WORK/systemd" "$WORK/sbin"
  mkdir -p "$WORK/caddy/acme" "$WORK/systemd"
  printf 'account\n' >"$WORK/caddy/acme/account.json"
  printf 'OLD UNIT\n' >"$GSM2COMPUTER_CADDY_UNIT_DEST"
  printf 'SHA256 Fingerprint=AA:AA\n' >"$WORK/fingerprint"
  : >"$GSM2COMPUTER_CADDY_ACTION_LOG"
  unset GSM2COMPUTER_CADDY_FINGERPRINT_ON_ENABLE
  unset GSM2COMPUTER_CADDY_FINGERPRINT_ON_START
}

run_migrate() {
  set +e
  bash "$MIGRATE" "$1" >"$WORK/out" 2>"$WORK/err"
  local rc=$?
  set -e
  printf '%s' "$rc"
}

note "non-empty destination"
reset_tree
mkdir -p "$GSM2COMPUTER_CADDY_STATE_NEW/already"
printf 'keep\n' >"$GSM2COMPUTER_CADDY_STATE_NEW/already/cert"
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "non-empty dest rc=$rc $(cat "$WORK/err")"
grep -q 'not empty' "$WORK/err" || fail "non-empty message: $(cat "$WORK/err")"
[[ -f "$WORK/caddy/acme/account.json" ]] || fail "source tree was moved"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/already/cert")" == "keep" ]] || fail "dest tree changed"
[[ ! -s "$GSM2COMPUTER_CADDY_ACTION_LOG" ]] || fail "stopped before refusing: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
note "  apply refused rc=1, Caddy still up"

note "empty destination is removed, then the tree moves"
reset_tree
mkdir -p "$GSM2COMPUTER_CADDY_STATE_NEW"
rc=$(run_migrate apply)
[[ "$rc" -eq 0 ]] || fail "empty dest rc=$rc $(cat "$WORK/err") $(cat "$WORK/out")"
[[ ! -e "$WORK/caddy" ]] || fail "old path still exists"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "account was not moved"
[[ ! -d "$GSM2COMPUTER_CADDY_STATE_NEW/caddy" ]] || fail "tree was nested"
grep -q 'XDG_DATA_HOME=/var/lib/caddy' "$GSM2COMPUTER_CADDY_UNIT_DEST" || fail "new unit was not installed"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_BACKUP")" == "OLD UNIT" ]] || fail "unit backup was not the previous unit"
grep -q 'enable --now' "$GSM2COMPUTER_CADDY_ACTION_LOG" || fail "did not enable --now"
note "  apply rc=0"

note "second apply does nothing"
cp "$GSM2COMPUTER_CADDY_UNIT_BACKUP" "$WORK/backup-copy"
: >"$GSM2COMPUTER_CADDY_ACTION_LOG"
rc=$(run_migrate apply)
[[ "$rc" -eq 0 ]] || fail "second apply rc=$rc $(cat "$WORK/err")"
grep -q 'already at' "$WORK/out" || fail "second apply message: $(cat "$WORK/out")"
[[ ! -s "$GSM2COMPUTER_CADDY_ACTION_LOG" ]] || fail "second apply touched systemd: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
cmp -s "$WORK/backup-copy" "$GSM2COMPUTER_CADDY_UNIT_BACKUP" || fail "second apply replaced the unit backup"
note "  second apply rc=0"

note "explicit rollback restores the directory and the unit"
printf 'SHA256 Fingerprint=AA:AA\n' >"$WORK/rollback-fingerprint"
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/rollback-fingerprint
printf 'SHA256 Fingerprint=AA:AA\n' >"$WORK/fingerprint"
rc=$(run_migrate rollback)
[[ "$rc" -eq 0 ]] || fail "rollback rc=$rc $(cat "$WORK/err") $(cat "$WORK/out")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "rollback lost the account"
[[ ! -e "$GSM2COMPUTER_CADDY_STATE_NEW" ]] || fail "new path still exists after rollback"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "unit was not restored"
grep -q '^stop ' "$GSM2COMPUTER_CADDY_ACTION_LOG" || fail "rollback did not stop"
grep -q '^start ' "$GSM2COMPUTER_CADDY_ACTION_LOG" || fail "rollback did not start"
note "  rollback rc=0"

note "fingerprint mismatch stops and rolls back"
reset_tree
printf 'SHA256 Fingerprint=BB:BB\n' >"$WORK/bad-fingerprint"
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_ENABLE=$WORK/bad-fingerprint
printf 'SHA256 Fingerprint=AA:AA\n' >"$WORK/good-fingerprint"
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/good-fingerprint
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "mismatch rc=$rc $(cat "$WORK/out")"
grep -q 'stopping Caddy' "$WORK/err" || fail "mismatch did not stop: $(cat "$WORK/err")"
grep -q 'rolling back' "$WORK/err" || fail "mismatch did not roll back: $(cat "$WORK/err")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "mismatch left the tree moved"
[[ ! -e "$GSM2COMPUTER_CADDY_STATE_NEW" ]] || fail "new path remains after mismatch rollback"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "mismatch left the new unit installed"
enable_line=$(grep -n 'enable --now' "$GSM2COMPUTER_CADDY_ACTION_LOG" | head -1 | cut -d: -f1)
stop_line=$(awk -v n="$enable_line" 'NR > n && /^stop / { print NR; exit }' "$GSM2COMPUTER_CADDY_ACTION_LOG")
[[ -n "$enable_line" && -n "$stop_line" && "$stop_line" -gt "$enable_line" ]] || fail "stop was not after enable: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
note "  mismatch rolled back rc=1"

note "rollback fingerprint mismatch leaves Caddy stopped"
reset_tree
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_ENABLE=$WORK/bad-fingerprint
printf 'SHA256 Fingerprint=CC:CC\n' >"$WORK/worse-fingerprint"
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/worse-fingerprint
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "rollback mismatch rc=$rc $(cat "$WORK/err")"
grep -q 'rollback served a different certificate' "$WORK/err" || fail "rollback mismatch message: $(cat "$WORK/err")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "rollback mismatch lost the account"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "rollback mismatch left the new unit"
tail -1 "$GSM2COMPUTER_CADDY_ACTION_LOG" | grep -q '^stop ' || fail "Caddy was not left stopped: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
note "  Caddy stopped rc=1"

note "caddy migrate ok"
