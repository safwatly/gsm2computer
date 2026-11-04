#!/usr/bin/env bash
# Idempotent Android SDK + Gradle cache for Cursor cloud agents.
#
# Matches app/build.gradle.kts (compileSdk 34) and CI
# (.github/workflows/ci.yml: platforms;android-34, build-tools;34.0.0)
# and the Gradle wrapper (gradle/wrapper/gradle-wrapper.properties: 8.5).
#
# Writes gitignored local.properties. Installs the headless emulator and an
# API 34 x86_64 AVD, and does not boot it (KVM needs a fresh login group).
# Re-run is safe. JDK 17+ must already be on PATH, or openjdk-21-jdk is installed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_ROOT="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
PACKAGES=(
  "platform-tools"
  "platforms;android-34"
  "build-tools;34.0.0"
  "emulator"
  "system-images;android-34;google_apis;x86_64"
)
# Host libraries the emulator 37 linker needs on Ubuntu 24.04. Names are the
# t64 packages on that release.
HOST_PACKAGES=(
  openjdk-21-jdk
  ca-certificates
  curl
  unzip
  libnss3
  libxcomposite1
  libxcursor1
  libxi6
  libxtst6
  libxrandr2
  libasound2t64
  libpulse0
  libgl1
  libxdamage1
  libxfixes3
  libxrender1
  libglib2.0-0t64
)

log() { printf '[android-sdk] %s\n' "$*"; }

missing=()
for pkg in "${HOST_PACKAGES[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if ((${#missing[@]})); then
  log "installing host packages: ${missing[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${missing[@]}"
fi

java_major=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d. -f1)
if [[ "${java_major:-0}" -lt 17 ]]; then
  echo "JDK 17+ required, found ${java_major}" >&2
  exit 1
fi

if [[ ! -x "$SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" ]]; then
  log "installing Android command-line tools into $SDK_ROOT"
  sudo mkdir -p "$SDK_ROOT/cmdline-tools"
  tmp=$(mktemp -d)
  curl -fsSL "$CMDLINE_TOOLS_URL" -o "$tmp/cmdtools.zip"
  unzip -qo "$tmp/cmdtools.zip" -d "$tmp"
  sudo rm -rf "$SDK_ROOT/cmdline-tools/latest"
  sudo mv "$tmp/cmdline-tools" "$SDK_ROOT/cmdline-tools/latest"
  rm -rf "$tmp"
fi

# sdkmanager and Gradle run as the agent user so caches land in $HOME.
sudo chown -R "$(id -un):$(id -gn)" "$SDK_ROOT"

export ANDROID_HOME="$SDK_ROOT"
export ANDROID_SDK_ROOT="$SDK_ROOT"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
if [[ -z "${JAVA_HOME:-}" ]]; then
  JAVA_HOME="$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")"
  export JAVA_HOME
fi

log "accepting SDK licenses"
set +o pipefail
yes | sdkmanager --sdk_root="$SDK_ROOT" --licenses >/dev/null
set -o pipefail

log "installing ${PACKAGES[*]}"
sdkmanager --sdk_root="$SDK_ROOT" --install "${PACKAGES[@]}"

if [[ ! -f "$HOME/.android/avd/gsm2computer.ini" ]]; then
  log "creating AVD gsm2computer (pixel_6, API 34 google_apis x86_64)"
  echo no | avdmanager create avd \
    -n gsm2computer \
    -k "system-images;android-34;google_apis;x86_64" \
    -d pixel_6
fi

# Login shells (bash -lc, which is how cloud-agent install/start run) source
# /etc/profile.d. ~/.bashrc is skipped when the shell is not interactive.
profile=/etc/profile.d/android-sdk.sh
sudo tee "$profile" >/dev/null <<EOF
export JAVA_HOME="${JAVA_HOME}"
export ANDROID_HOME=${SDK_ROOT}
export ANDROID_SDK_ROOT=${SDK_ROOT}
export PATH="\$JAVA_HOME/bin:\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools:\$ANDROID_HOME/emulator:\$PATH"
EOF
sudo chmod 644 "$profile"

# /dev/kvm is created by the host as root:<gid> mode 660. The gid often has
# no name in the image. Name it and join it so later logins can use KVM.
if [[ -e /dev/kvm ]]; then
  kvm_gid="$(stat -c '%g' /dev/kvm)"
  gname="$(getent group "$kvm_gid" | cut -d: -f1 || true)"
  if [[ -z "$gname" ]]; then
    gname=kvm
    if ! getent group "$gname" >/dev/null; then
      sudo groupadd -g "$kvm_gid" "$gname" || sudo groupadd "$gname"
    fi
  fi
  sudo usermod -aG "$gname" "$(id -un)" || true
  log "kvm device gid=$kvm_gid group=$gname (membership applies to new logins)"
fi

cd "$ROOT"
printf 'sdk.dir=%s\n' "$SDK_ROOT" > local.properties
log "wrote $ROOT/local.properties"

if [[ ! -f "$HOME/.android/debug.keystore" ]]; then
  mkdir -p "$HOME/.android"
  keytool -genkey -v \
    -keystore "$HOME/.android/debug.keystore" \
    -storepass android \
    -alias androiddebugkey \
    -keypass android \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US"
  log "created ~/.android/debug.keystore (release signing uses the debug key)"
fi

log "warming Gradle 8.5 wrapper and debug compile (no test run)"
./gradlew --no-daemon assembleDebug compileDebugUnitTestSources compileDebugAndroidTestSources

log "ready ANDROID_HOME=$SDK_ROOT"
sdkmanager --sdk_root="$SDK_ROOT" --list_installed
