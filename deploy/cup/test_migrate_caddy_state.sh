#!/usr/bin/env bash
# The copy leaves /caddy in place. A failed start or a new fingerprint puts
# the previous unit back without moving either tree. The fingerprint lives
# under the state dir, so wiping /tmp does not matter. A slow TLS listener
# is retried until it answers; silence until the wait cap is a failure.
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
mkdir -p "$BIN" "$WORK/tmp"

cat >"$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$GSM2COMPUTER_CADDY_ACTION_LOG"
if [[ "${1:-}" == "start" && -f "${GSM2COMPUTER_CADDY_FAIL_START_FLAG:-}" ]]; then
  rm -f "$GSM2COMPUTER_CADDY_FAIL_START_FLAG"
  exit 1
fi
if [[ "${1:-}" == "start" && -f "${GSM2COMPUTER_CADDY_FINGERPRINT_ON_START:-}" ]]; then
  cp "$GSM2COMPUTER_CADDY_FINGERPRINT_ON_START" "$GSM2COMPUTER_CADDY_FINGERPRINT_NOW"
fi
if [[ "${1:-}" == "start" && -n "${GSM2COMPUTER_CADDY_TLS_SLOW_READS:-}" ]]; then
  printf '%s\n' "$GSM2COMPUTER_CADDY_TLS_SLOW_READS" >"$GSM2COMPUTER_CADDY_TLS_SLOW_LEFT"
fi
exit 0
EOF
cat >"$BIN/fingerprint" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
left=${GSM2COMPUTER_CADDY_TLS_SLOW_LEFT:-}
if [[ -n "$left" && -f "$left" ]]; then
  n=$(cat "$left")
  if [[ "$n" -gt 0 ]]; then
    printf '%s\n' $((n - 1)) >"$left"
    # The last miss is an empty read (what left gsm-cert.after empty).
    # Earlier misses are connection refused, which is what openssl s_client
    # returns before the listener accepts.
    if [[ "$n" -eq 1 ]]; then
      exit 0
    fi
    exit 1
  fi
fi
cat "$GSM2COMPUTER_CADDY_FINGERPRINT_NOW"
EOF
cat >"$BIN/sleep" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'sleep %s\n' "$1" >>"$GSM2COMPUTER_CADDY_ACTION_LOG"
exit 0
EOF
chmod 755 "$BIN/systemctl" "$BIN/fingerprint" "$BIN/sleep"

export PATH="$BIN:/usr/bin:/bin"
export GSM2COMPUTER_CADDY_STATE_OLD=$WORK/caddy
export GSM2COMPUTER_CADDY_STATE_NEW=$WORK/var/lib/caddy/caddy
export GSM2COMPUTER_CADDY_UNIT_DEST=$WORK/systemd/gsm2computer-caddy.service
export GSM2COMPUTER_CADDY_UNIT_SRC=$ROOT/deploy/cup/gsm2computer-caddy.service
export GSM2COMPUTER_CADDY_UNIT_BACKUP=$WORK/var/lib/caddy/gsm2computer-caddy.service.bak
export GSM2COMPUTER_CADDY_PRE_SRC=$ROOT/deploy/cup/gsm2computer-caddy-pre.sh
export GSM2COMPUTER_CADDY_PRE_DEST=$WORK/sbin/gsm2computer-caddy-pre.sh
export GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE=$WORK/var/lib/caddy/gsm-cert.before
export GSM2COMPUTER_CADDY_FINGERPRINT_AFTER=$WORK/var/lib/caddy/gsm-cert.after
export GSM2COMPUTER_CADDY_FINGERPRINT_CMD=$BIN/fingerprint
export GSM2COMPUTER_CADDY_FINGERPRINT_NOW=$WORK/fingerprint
export GSM2COMPUTER_CADDY_TLS_SLOW_LEFT=$WORK/tls-slow-left
export GSM2COMPUTER_CADDY_ACTION_LOG=$WORK/actions
export GSM2COMPUTER_CADDY_FAIL_START_FLAG=$WORK/fail-start

reset_tree() {
  rm -rf "$WORK/caddy" "$WORK/var" "$WORK/systemd" "$WORK/sbin" "$WORK/tmp"
  mkdir -p "$WORK/caddy/acme" "$WORK/systemd" "$WORK/tmp"
  printf 'account\n' >"$WORK/caddy/acme/account.json"
  printf 'OLD UNIT\n' >"$GSM2COMPUTER_CADDY_UNIT_DEST"
  printf 'SHA256 Fingerprint=AA:AA\n' >"$WORK/fingerprint"
  printf 'SHA256 Fingerprint=AA:AA\n' >"$WORK/good-fingerprint"
  printf 'SHA256 Fingerprint=BB:BB\n' >"$WORK/bad-fingerprint"
  : >"$GSM2COMPUTER_CADDY_ACTION_LOG"
  rm -f "$GSM2COMPUTER_CADDY_FAIL_START_FLAG" "$GSM2COMPUTER_CADDY_TLS_SLOW_LEFT"
  unset GSM2COMPUTER_CADDY_FINGERPRINT_ON_START
  unset GSM2COMPUTER_CADDY_TLS_SLOW_READS
}

sleep_total() {
  awk '/^sleep / { s += $2 } END { print s + 0 }' "$GSM2COMPUTER_CADDY_ACTION_LOG"
}

run_migrate() {
  set +e
  bash "$MIGRATE" "$1" >"$WORK/out" 2>"$WORK/err"
  local rc=$?
  set -e
  printf '%s' "$rc"
}

note "non-empty different destination"
reset_tree
mkdir -p "$GSM2COMPUTER_CADDY_STATE_NEW/already"
printf 'other\n' >"$GSM2COMPUTER_CADDY_STATE_NEW/already/cert"
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "conflict rc=$rc $(cat "$WORK/err")"
grep -q 'non-empty and differs' "$WORK/err" || fail "conflict message: $(cat "$WORK/err")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "source changed"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/already/cert")" == "other" ]] || fail "dest changed"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "unit changed"
[[ ! -s "$GSM2COMPUTER_CADDY_ACTION_LOG" ]] || fail "stopped before refusing: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
note "  apply refused rc=1, Caddy still up"

note "copy leaves /caddy in place"
reset_tree
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/good-fingerprint
rc=$(run_migrate apply)
[[ "$rc" -eq 0 ]] || fail "apply rc=$rc $(cat "$WORK/err") $(cat "$WORK/out")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "/caddy was removed"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "copy missed the account"
grep -q 'XDG_DATA_HOME=/var/lib/caddy' "$GSM2COMPUTER_CADDY_UNIT_DEST" || fail "new unit was not installed"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_BACKUP")" == "OLD UNIT" ]] || fail "unit backup missing"
[[ "$(cat "$GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE")" == "SHA256 Fingerprint=AA:AA" ]] || fail "fingerprint was not saved under the state dir"
grep -q '^start ' "$GSM2COMPUTER_CADDY_ACTION_LOG" || fail "did not start"
note "  apply rc=0, both trees present"

note "start failing restores the old unit"
reset_tree
touch "$GSM2COMPUTER_CADDY_FAIL_START_FLAG"
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/good-fingerprint
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "start-fail rc=$rc $(cat "$WORK/err") $(cat "$WORK/out")"
grep -q 'start failed' "$WORK/err" || fail "start-fail message: $(cat "$WORK/err")"
[[ "$(sleep_total)" -eq 0 ]] || fail "start-fail waited for TLS: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "start-fail touched /caddy"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "start-fail did not keep the copy"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "start-fail left the new unit"
start_lines=$(grep -c '^start ' "$GSM2COMPUTER_CADDY_ACTION_LOG")
[[ "$start_lines" -eq 2 ]] || fail "expected the failed start and the restore start: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
note "  old unit restarted rc=1"

note "fingerprint mismatch restores the old unit"
reset_tree
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/bad-fingerprint
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "mismatch rc=$rc $(cat "$WORK/out") $(cat "$WORK/err")"
grep -q 'fingerprint changed' "$WORK/err" || fail "mismatch message: $(cat "$WORK/err")"
[[ "$(sleep_total)" -eq 0 ]] || fail "mismatch retried a certificate that already answered: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "mismatch touched /caddy"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "mismatch removed the copy"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "mismatch left the new unit"
note "  old unit restored rc=1"

note "tls slow to answer, then the same certificate"
reset_tree
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/good-fingerprint
export GSM2COMPUTER_CADDY_TLS_SLOW_READS=3
rc=$(run_migrate apply)
[[ "$rc" -eq 0 ]] || fail "slow-tls rc=$rc $(cat "$WORK/err") $(cat "$WORK/out")"
[[ "$(sleep_total)" -eq 7 ]] || fail "slow-tls backoff was $(sleep_total), want 1+2+4: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
[[ "$(grep -c '^start ' "$GSM2COMPUTER_CADDY_ACTION_LOG")" -eq 1 ]] || fail "slow-tls restored the unit: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
grep -q 'XDG_DATA_HOME=/var/lib/caddy' "$GSM2COMPUTER_CADDY_UNIT_DEST" || fail "slow-tls did not keep the new unit"
cmp -s "$GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE" "$GSM2COMPUTER_CADDY_FINGERPRINT_AFTER" || fail "slow-tls compared before TLS answered"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "slow-tls touched /caddy"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "slow-tls missed the copy"
note "  apply rc=0 after refused and empty reads"

note "tls silent for the whole wait restores the old unit"
reset_tree
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/good-fingerprint
export GSM2COMPUTER_CADDY_TLS_SLOW_READS=1000
rc=$(run_migrate apply)
[[ "$rc" -eq 1 ]] || fail "tls-silent rc=$rc $(cat "$WORK/out") $(cat "$WORK/err")"
grep -q 'did not answer' "$WORK/err" || fail "tls-silent message: $(cat "$WORK/err")"
[[ "$(sleep_total)" -eq 30 ]] || fail "tls-silent waited $(sleep_total)s, want 30: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
[[ ! -s "$GSM2COMPUTER_CADDY_FINGERPRINT_AFTER" ]] || fail "tls-silent wrote a fingerprint: $(cat "$GSM2COMPUTER_CADDY_FINGERPRINT_AFTER")"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "tls-silent left the new unit"
[[ "$(grep -c '^start ' "$GSM2COMPUTER_CADDY_ACTION_LOG")" -eq 2 ]] || fail "tls-silent did not restart the old unit: $(cat "$GSM2COMPUTER_CADDY_ACTION_LOG")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "tls-silent touched /caddy"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "tls-silent removed the copy"
note "  old unit restored after 30s rc=1"

note "rollback with both trees, after /tmp is wiped"
reset_tree
export GSM2COMPUTER_CADDY_FINGERPRINT_ON_START=$WORK/good-fingerprint
rc=$(run_migrate apply)
[[ "$rc" -eq 0 ]] || fail "pre-rollback apply rc=$rc $(cat "$WORK/err")"
printf 'SHA256 Fingerprint=WRONG\n' >"$WORK/tmp/gsm-cert.before"
printf 'SHA256 Fingerprint=WRONG\n' >/tmp/gsm-cert.before
rm -rf "$WORK/tmp" /tmp/gsm-cert.before
: >"$GSM2COMPUTER_CADDY_ACTION_LOG"
rc=$(run_migrate rollback)
[[ "$rc" -eq 0 ]] || fail "rollback rc=$rc $(cat "$WORK/err") $(cat "$WORK/out")"
grep -q 'certificate unchanged' "$WORK/out" || fail "rollback message: $(cat "$WORK/out")"
[[ "$(cat "$WORK/caddy/acme/account.json")" == "account" ]] || fail "rollback removed /caddy"
[[ "$(cat "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json")" == "account" ]] || fail "rollback removed the copy"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "rollback did not restore the unit"
[[ "$(cat "$GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE")" == "SHA256 Fingerprint=AA:AA" ]] || fail "saved fingerprint was not under the state dir"
grep -q '^stop ' "$GSM2COMPUTER_CADDY_ACTION_LOG" || fail "rollback did not stop"
grep -q '^start ' "$GSM2COMPUTER_CADDY_ACTION_LOG" || fail "rollback did not start"
note "  both trees kept, fingerprint read from the state dir"

note "rollback with no saved fingerprint still restores"
rm -f "$GSM2COMPUTER_CADDY_FINGERPRINT_BEFORE"
: >"$GSM2COMPUTER_CADDY_ACTION_LOG"
rc=$(run_migrate rollback)
[[ "$rc" -eq 0 ]] || fail "no-fingerprint rollback rc=$rc $(cat "$WORK/err")"
grep -q 'no saved fingerprint' "$WORK/out" || fail "no-fingerprint message: $(cat "$WORK/out")"
[[ "$(cat "$GSM2COMPUTER_CADDY_UNIT_DEST")" == "OLD UNIT" ]] || fail "no-fingerprint rollback skipped the unit"
[[ -f "$WORK/caddy/acme/account.json" && -f "$GSM2COMPUTER_CADDY_STATE_NEW/acme/account.json" ]] || fail "no-fingerprint rollback touched a tree"
note "  reported and restored rc=0"

note "caddy migrate ok"
