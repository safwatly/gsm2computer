#!/bin/bash
# write_sdk_dir keeps GSM2COMPUTER_* lines, and a release signed with the
# debug certificate fails. Does not build an APK.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/build.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

cat >"$tmp/local.properties" <<'EOF'
# signing keys stay
GSM2COMPUTER_STORE_FILE=signing/gsm2computer-upload.keystore
GSM2COMPUTER_STORE_PASSWORD=not-a-real-secret
GSM2COMPUTER_KEY_PASSWORD=also-not
sdk.dir=/old/sdk
sdk.dir=/older/sdk
EOF

write_sdk_dir "$tmp/local.properties" /opt/android-sdk

grep -q '^# signing keys stay$' "$tmp/local.properties" || fail "comment dropped"
grep -q '^GSM2COMPUTER_STORE_FILE=signing/gsm2computer-upload.keystore$' "$tmp/local.properties" || fail "store file dropped"
grep -q '^GSM2COMPUTER_STORE_PASSWORD=not-a-real-secret$' "$tmp/local.properties" || fail "store password dropped"
grep -q '^GSM2COMPUTER_KEY_PASSWORD=also-not$' "$tmp/local.properties" || fail "key password dropped"
grep -q '^sdk.dir=/opt/android-sdk$' "$tmp/local.properties" || fail "sdk.dir not updated"
if grep -q '/old' "$tmp/local.properties"; then
    fail "old sdk.dir left in place"
fi
count="$(grep -c '^sdk.dir=' "$tmp/local.properties")"
if [ "$count" != 1 ]; then
    fail "expected one sdk.dir line, found $count"
fi

printf 'GSM2COMPUTER_KEY_ALIAS=androiddebugkey' >"$tmp/plain.properties"
write_sdk_dir "$tmp/plain.properties" /opt/android-sdk
grep -q '^GSM2COMPUTER_KEY_ALIAS=androiddebugkey$' "$tmp/plain.properties" || fail "alias dropped on append"
grep -q '^sdk.dir=/opt/android-sdk$' "$tmp/plain.properties" || fail "sdk.dir not appended"

write_sdk_dir "$tmp/missing.properties" /new/sdk
grep -q '^sdk.dir=/new/sdk$' "$tmp/missing.properties" || fail "missing file not created"

if grep -nE 'echo "sdk\.dir=.*" > local\.properties' "$ROOT/build.sh" >/dev/null; then
    fail "build.sh still truncates local.properties"
fi

err="$(mktemp)"
if assert_signer release "aa:bb:cc" "" "" >"$tmp/out" 2>"$err"; then
    fail "release signed with the debug keystore should fail"
fi
grep -q "debug certificate" "$err" || fail "release failure did not name the debug certificate"
grep -q "Signer SHA-256: aabbcc" "$tmp/out" || fail "signer SHA-256 was not printed"

if assert_signer release "0123456789abcdef" "/tmp/upload.keystore" "" >"$tmp/out" 2>"$err"; then
    :
else
    fail "release with a non-debug keystore should pass"
fi
grep -q "Signer SHA-256: 0123456789abcdef" "$tmp/out" || fail "non-debug SHA-256 was not printed"

if assert_signer debug "abcd" "/tmp/upload.keystore" "ffff" >"$tmp/out" 2>"$err"; then
    fail "expected-cert mismatch should fail"
fi
grep -q "does not match expected ffff" "$err" || fail "expected-cert mismatch was quiet"

if assert_signer release "AB:CD" "/tmp/upload.keystore" "abcd" >"$tmp/out" 2>"$err"; then
    :
else
    fail "expected-cert match should pass"
fi
grep -q "matches expected cert" "$tmp/out" || fail "expected-cert match was not confirmed"

# A release pinned to the debug cert still fails. The debug check wins.
if assert_signer release "aa" "" "aa" >"$tmp/out" 2>"$err"; then
    fail "release expect-cert of the debug cert should still fail"
fi

echo "build.sh local.properties and signer checks ok"
