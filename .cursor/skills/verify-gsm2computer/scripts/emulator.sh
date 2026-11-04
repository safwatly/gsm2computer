#!/usr/bin/env bash
# Boot the headless API 34 AVD created by scripts/cloud-agent-android-sdk.sh.
# Does not install the SDK. Does not kill an emulator that is already up.
set -euo pipefail

ANDROID_HOME="${ANDROID_HOME:-/opt/android-sdk}"
export ANDROID_HOME ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

LOG=/tmp/gsm2computer-emulator.log
PIDFILE=/tmp/gsm2computer-emulator.pid

if [[ ! -x "$ANDROID_HOME/emulator/emulator" ]]; then
  echo "emulator binary missing under $ANDROID_HOME — run scripts/cloud-agent-android-sdk.sh" >&2
  exit 1
fi
if [[ ! -f "$HOME/.android/avd/gsm2computer.ini" ]]; then
  echo "AVD gsm2computer is missing — run scripts/cloud-agent-android-sdk.sh" >&2
  exit 1
fi

if adb devices | grep -q '^emulator-5554[[:space:]]*device'; then
  boot=$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
  if [[ "$boot" == "1" ]]; then
    echo "already booted emulator-5554"
    exit 0
  fi
fi

if ! sg kvm -c 'test -r /dev/kvm && test -w /dev/kvm'; then
  echo "cannot open /dev/kvm (mode $(stat -c '%A' /dev/kvm 2>/dev/null || echo missing), groups $(id -nG))" >&2
  echo "scripts/cloud-agent-android-sdk.sh joins the kvm group; that applies to a new login. Use sg kvm." >&2
  exit 1
fi

echo "booting gsm2computer (log $LOG)"
sg kvm -c "ANDROID_HOME=$ANDROID_HOME ANDROID_SDK_ROOT=$ANDROID_SDK_ROOT $ANDROID_HOME/emulator/emulator -avd gsm2computer -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect -accel on -no-snapshot -no-metrics -ports 5554,5555" >"$LOG" 2>&1 &
echo $! >"$PIDFILE"

adb wait-for-device
for _ in $(seq 1 90); do
  boot=$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
  if [[ "$boot" == "1" ]]; then
    echo "booted emulator-5554 pid $(cat "$PIDFILE")"
    exit 0
  fi
  sleep 2
done

echo "emulator did not finish booting; see $LOG" >&2
exit 1
