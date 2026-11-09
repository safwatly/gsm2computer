#!/bin/bash
#
# Build script for GSM2Computer Bridge APK + Magisk module.
#
# Prerequisites:
#   sudo ./setup.sh    # run once to install JDK, Android SDK, etc.
#
# Usage:
#   ./build.sh          # Build debug APK + Magisk module
#   ./build.sh release  # Build release APK + Magisk module
#   ./build.sh release --expect-cert <sha256>
#
# `release` fails if the APK signer is the Android debug certificate.
# GSM2COMPUTER_EXPECT_CERT is the same check as --expect-cert.
#
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Replace sdk.dir, or append it. Every other line stays, including
# GSM2COMPUTER_* signing keys. Rewriting the file with `>` drops those keys
# and the release build then signs with the debug certificate.
write_sdk_dir() {
    local file="$1"
    local sdk_dir="$2"
    local tmp
    tmp="$(mktemp)"
    if [ -f "$file" ]; then
        awk -v sdk="$sdk_dir" '
            BEGIN { found = 0 }
            /^[[:space:]]*sdk\.dir[[:space:]]*=/ {
                if (!found) print "sdk.dir=" sdk
                found = 1
                next
            }
            { print }
            END { if (!found) print "sdk.dir=" sdk }
        ' "$file" > "$tmp"
    else
        printf 'sdk.dir=%s\n' "$sdk_dir" > "$tmp"
    fi
    mv "$tmp" "$file"
}

normalize_cert_sha() {
    printf '%s' "$1" | tr -d '[:space:]:' | tr 'A-F' 'a-f'
}

apk_signer_sha256() {
    local apk="$1"
    local signer="$2"
    "$signer" verify --print-certs "$apk" 2>/dev/null \
        | awk -F': ' '/SHA-256 digest:/ { print $2; exit }'
}

debug_keystore_sha256() {
    local ks="${1:-$HOME/.android/debug.keystore}"
    if [ ! -f "$ks" ] || ! command -v keytool >/dev/null 2>&1; then
        return 0
    fi
    keytool -list -v -keystore "$ks" -storepass android -alias androiddebugkey 2>/dev/null \
        | awk -F': ' '/SHA256:/ { print $2; exit }' || true
}

# Print the signer SHA-256. A release signed with the debug cert fails.
# expect_sha (or GSM2COMPUTER_EXPECT_CERT, passed in) must match when set.
# Returns 1 on a mismatch. Does not exit, so tests can call it.
assert_signer() {
    local build_type="$1"
    local signer_sha="$2"
    local store_file="$3"
    local expect_sha="${4:-}"
    local norm_signer norm_expect debug_sha debug_ks
    norm_signer="$(normalize_cert_sha "$signer_sha")"
    echo "Signer SHA-256: $norm_signer"
    debug_ks="${HOME}/.android/debug.keystore"
    local is_debug=0
    if [ -z "$store_file" ] || [ "$store_file" = "$debug_ks" ]; then
        is_debug=1
    else
        debug_sha="$(debug_keystore_sha256 "$debug_ks")"
        if [ -n "$debug_sha" ] && [ "$norm_signer" = "$(normalize_cert_sha "$debug_sha")" ]; then
            is_debug=1
        fi
    fi
    if [ "$build_type" = "release" ] && [ "$is_debug" = 1 ]; then
        echo "ERROR: release build is signed with the Android debug certificate." >&2
        echo "Signer SHA-256: $norm_signer" >&2
        echo "A deployable 345 needs the upload keystore. Set GSM2COMPUTER_STORE_FILE" >&2
        echo "in the environment or local.properties (see README)." >&2
        echo "Pin it with --expect-cert or GSM2COMPUTER_EXPECT_CERT." >&2
        return 1
    fi
    if [ -n "$expect_sha" ]; then
        norm_expect="$(normalize_cert_sha "$expect_sha")"
        if [ "$norm_signer" != "$norm_expect" ]; then
            echo "ERROR: signer SHA-256 $norm_signer does not match expected $norm_expect" >&2
            return 1
        fi
        echo "Signer SHA-256 matches expected cert"
    fi
    return 0
}

# ── Check prerequisites ──────────────────────────────

check_java() {
    if ! command -v java &>/dev/null; then
        echo "ERROR: Java not found. Run: sudo ./setup.sh"
        exit 1
    fi
    JAVA_VER=$(java -version 2>&1 | head -1 | cut -d'"' -f2 | cut -d'.' -f1)
    if [ "$JAVA_VER" -lt 17 ] 2>/dev/null; then
        echo "ERROR: JDK 17+ required (found: $JAVA_VER). Run: sudo ./setup.sh"
        exit 1
    fi
    echo "Java: $(java -version 2>&1 | head -1)"
}

check_android_sdk() {
    # Ignore stale ANDROID_HOME / ANDROID_SDK_ROOT that point at a missing dir
    # (e.g. $HOME/Android/sdk when the real SDK is /opt/android-sdk).
    if [ -n "${ANDROID_HOME:-}" ] && [ ! -d "$ANDROID_HOME" ]; then
        echo "WARNING: ANDROID_HOME=$ANDROID_HOME does not exist; ignoring"
        unset ANDROID_HOME
    fi
    if [ -n "${ANDROID_SDK_ROOT:-}" ] && [ ! -d "$ANDROID_SDK_ROOT" ]; then
        echo "WARNING: ANDROID_SDK_ROOT=$ANDROID_SDK_ROOT does not exist; ignoring"
        unset ANDROID_SDK_ROOT
    fi
    if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
        for dir in "/opt/android-sdk" "$HOME/Android/Sdk" "$HOME/android-sdk"; do
            if [ -d "$dir" ]; then
                export ANDROID_HOME="$dir"
                break
            fi
        done
    fi
    ANDROID_HOME="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
    export ANDROID_HOME
    export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"

    if [ -z "$ANDROID_HOME" ] || [ ! -d "$ANDROID_HOME" ]; then
        echo "ERROR: Android SDK not found. Run: sudo ./setup.sh"
        exit 1
    fi

    # Keep GSM2COMPUTER_* lines. Only sdk.dir is rewritten.
    write_sdk_dir "$SCRIPT_DIR/local.properties" "$ANDROID_HOME"
    echo "Android SDK: $ANDROID_HOME"
}

check_gradle_wrapper() {
    local JAR="$SCRIPT_DIR/gradle/wrapper/gradle-wrapper.jar"
    local NEED_DOWNLOAD=false

    if [ ! -f "$JAR" ]; then
        NEED_DOWNLOAD=true
    elif ! unzip -t "$JAR" >/dev/null 2>&1; then
        echo "WARNING: gradle-wrapper.jar is corrupt, re-downloading..."
        rm -f "$JAR"
        NEED_DOWNLOAD=true
    fi

    if [ "$NEED_DOWNLOAD" = true ]; then
        echo "Downloading Gradle wrapper..."
        mkdir -p "$SCRIPT_DIR/gradle/wrapper"

        local GRADLE_VER="8.5"
        local GRADLE_URL="https://services.gradle.org/distributions/gradle-${GRADLE_VER}-bin.zip"

        curl -fsSL "$GRADLE_URL" -o /tmp/gradle-dist.zip
        rm -rf /tmp/gradle-extract
        mkdir -p /tmp/gradle-extract
        unzip -qo /tmp/gradle-dist.zip -d /tmp/gradle-extract

        local GRADLE_BIN="/tmp/gradle-extract/gradle-${GRADLE_VER}/bin/gradle"
        if [ -x "$GRADLE_BIN" ]; then
            cd "$SCRIPT_DIR"
            "$GRADLE_BIN" wrapper --gradle-version "$GRADLE_VER"
        fi

        rm -f /tmp/gradle-dist.zip
        rm -rf /tmp/gradle-extract
    fi

    if [ ! -f "$JAR" ] || ! unzip -t "$JAR" >/dev/null 2>&1; then
        echo "ERROR: Valid gradle-wrapper.jar not found. Run: sudo ./setup.sh"
        exit 1
    fi

    chmod +x "$SCRIPT_DIR/gradlew" 2>/dev/null || true
}

# ── Build APK ────────────────────────────────────────

build_apk() {
    local BUILD_TYPE="${1:-debug}"
    echo ""
    echo "=== Building $BUILD_TYPE APK ==="
    echo ""

    if [ "$BUILD_TYPE" = "release" ]; then
        ./gradlew assembleRelease --no-daemon
        APK_PATH="app/build/outputs/apk/release/app-release.apk"
        if [ ! -f "$APK_PATH" ]; then
            APK_PATH="app/build/outputs/apk/release/app-release-unsigned.apk"
        fi
    else
        ./gradlew assembleDebug --no-daemon
        APK_PATH="app/build/outputs/apk/debug/app-debug.apk"
    fi

    if [ -f "$APK_PATH" ]; then
        echo ""
        echo "APK built: $APK_PATH"
        cp "$APK_PATH" "$SCRIPT_DIR/gateway.apk"
        echo "Copied to: $SCRIPT_DIR/gateway.apk"
        # Sign the APK so it can be installed/updated as a system priv-app.
        # An unsigned release build fails pm install with NO_CERTIFICATES and
        # cannot be used as the basis for a Magisk priv-app overlay update.
        sign_apk "$SCRIPT_DIR/gateway.apk" "$BUILD_TYPE"
    else
        echo "ERROR: APK not found at $APK_PATH"
        exit 1
    fi
}

# ── Sign APK ─────────────────────────────────────────

sign_apk() {
    local APK="$1"
    local SIGN_BUILD_TYPE="${2:-${BUILD_TYPE:-debug}}"
    local APKSIGNER=""
    for candidate in \
        "$ANDROID_HOME/build-tools"/*/apksigner \
        "$ANDROID_SDK_ROOT"/build-tools/*/apksigner \
        /opt/android-sdk/build-tools/*/apksigner \
        "$HOME/Android/Sdk/build-tools"/*/apksigner; do
        if [ -x "$candidate" ] 2>/dev/null; then APKSIGNER="$candidate"; break; fi
    done
    if [ -z "$APKSIGNER" ]; then
        if [ "$SIGN_BUILD_TYPE" = "release" ]; then
            echo "ERROR: apksigner not found; refusing an unsigned release APK" >&2
            return 1
        fi
        echo "WARNING: apksigner not found; APK left unsigned (priv-app install will fail)"
        return 0
    fi
    local KS ALIAS
    KS="$(gsm_signing GSM2COMPUTER_STORE_FILE)"
    if [ -z "$KS" ]; then
        KS="${GATEWAY_KEYSTORE:-$HOME/.android/debug.keystore}"
        export GSM2COMPUTER_STORE_PASSWORD=android
        export GSM2COMPUTER_KEY_PASSWORD=android
        ALIAS=androiddebugkey
        echo "Signing with the debug keystore (set GSM2COMPUTER_STORE_FILE for a deployable build)"
    else
        export GSM2COMPUTER_STORE_PASSWORD="$(gsm_signing GSM2COMPUTER_STORE_PASSWORD)"
        export GSM2COMPUTER_KEY_PASSWORD="$(gsm_signing GSM2COMPUTER_KEY_PASSWORD)"
        ALIAS="$(gsm_signing GSM2COMPUTER_KEY_ALIAS)"
        ALIAS="${ALIAS:-androiddebugkey}"
        if [ -z "$GSM2COMPUTER_STORE_PASSWORD" ] || [ -z "$GSM2COMPUTER_KEY_PASSWORD" ]; then
            echo "ERROR: GSM2COMPUTER_STORE_PASSWORD and GSM2COMPUTER_KEY_PASSWORD are required" >&2
            echo "Put them in local.properties or the environment. Do not commit them." >&2
            exit 1
        fi
        echo "Signing with $KS alias $ALIAS"
    fi
    if [ ! -f "$KS" ]; then
        if [ "$SIGN_BUILD_TYPE" = "release" ]; then
            echo "ERROR: keystore not found at $KS; refusing an unsigned release APK" >&2
            return 1
        fi
        echo "WARNING: keystore not found at $KS; APK left unsigned"
        return 0
    fi
    echo "Signing APK with $APKSIGNER ..."
    if ! "$APKSIGNER" sign --ks "$KS" \
        --ks-pass env:GSM2COMPUTER_STORE_PASSWORD \
        --key-pass env:GSM2COMPUTER_KEY_PASSWORD \
        --ks-key-alias "$ALIAS" "$APK"; then
        echo "ERROR: APK signing failed" >&2
        return 1
    fi
    echo "APK signed"
    local sha
    sha="$(apk_signer_sha256 "$APK" "$APKSIGNER")"
    if [ -z "$sha" ]; then
        echo "ERROR: apksigner did not print a signer SHA-256 for $APK" >&2
        return 1
    fi
    assert_signer "$SIGN_BUILD_TYPE" "$sha" "$KS" "${EXPECT_CERT:-}"
}

# Env wins over gitignored local.properties. Prints the value only.
gsm_signing() {
    local key="$1"
    local value="${!key:-}"
    if [ -n "$value" ]; then
        printf '%s' "$value"
        return 0
    fi
    local file="$SCRIPT_DIR/local.properties"
    if [ -f "$file" ]; then
        local line
        line="$(grep -m1 "^${key}=" "$file" || true)"
        printf '%s' "${line#*=}"
    fi
}

# ── Build Magisk module ─────────────────────────────

build_tinymix() {
    echo ""
    echo "=== Building tinymix (ARM64 static binary) ==="
    echo ""

    local TINYMIX_SRC="$SCRIPT_DIR/tools/tinymix"
    local TINYMIX_BIN="$SCRIPT_DIR/magisk/tinymix"

    # If a pre-built binary already exists in magisk/, use it
    if [ -f "$TINYMIX_BIN" ]; then
        local ARCH=$(file "$TINYMIX_BIN" 2>/dev/null)
        if echo "$ARCH" | grep -q "ARM aarch64"; then
            echo "Using existing tinymix binary: $TINYMIX_BIN"
            return 0
        fi
    fi

    # Try to build with Go (cross-compiles to ARM64 easily)
    if command -v go &>/dev/null; then
        if [ -d "$TINYMIX_SRC" ] && [ -f "$TINYMIX_SRC/main.go" ]; then
            echo "Building tinymix with Go..."
            (cd "$TINYMIX_SRC" && GOOS=linux GOARCH=arm64 CGO_ENABLED=0 \
                go build -ldflags='-s -w' -o "$TINYMIX_BIN" .)
            if [ -f "$TINYMIX_BIN" ]; then
                chmod 755 "$TINYMIX_BIN"
                echo "tinymix built: $TINYMIX_BIN ($(du -h "$TINYMIX_BIN" | cut -f1))"
                return 0
            fi
        fi
    fi

    echo "WARNING: Could not build tinymix. ABOX mixer controls may not work."
    echo "         Install Go or place a pre-built ARM64 tinymix at: $TINYMIX_BIN"
    return 1
}

build_magisk() {
    echo ""
    echo "=== Building Magisk module ==="
    echo ""

    # Build tinymix binary for ABOX mixer control (required on Samsung Exynos)
    build_tinymix

    # Copy the APK into the Magisk module as a system priv-app.
    # This makes the app a privileged system app, enabling permissions
    # like CAPTURE_AUDIO_OUTPUT that are required for telephony audio capture.
    mkdir -p "$SCRIPT_DIR/magisk/system/priv-app/Gateway"
    cp "$SCRIPT_DIR/gateway.apk" "$SCRIPT_DIR/magisk/system/priv-app/Gateway/Gateway.apk"
    echo "Included APK as priv-app in Magisk module"

    cd "$SCRIPT_DIR/magisk"
    rm -f "$SCRIPT_DIR/gateway-magisk.zip"
    zip -r "$SCRIPT_DIR/gateway-magisk.zip" . \
        -x "*.DS_Store" -x "__MACOSX/*"
    echo "Magisk module: $SCRIPT_DIR/gateway-magisk.zip"
    cd "$SCRIPT_DIR"
}

# ── Install to device (if connected via ADB) ────────

install_to_device() {
    # Magisk priv-app deploys go through deploy.sh. This user-app install is
    # optional and must not fail the build when several devices are attached.
    if ! command -v adb &>/dev/null; then
        echo ""
        echo "No adb in PATH. APK is at $SCRIPT_DIR/gateway.apk"
        return
    fi
    mapfile -t devices < <(adb devices 2>/dev/null | awk 'NR>1 && $2=="device" {print $1}')
    if [ "${#devices[@]}" -eq 0 ]; then
        echo ""
        echo "No ADB device connected. To install manually:"
        echo "  ./deploy.sh -s SERIAL"
        return
    fi
    if [ "${#devices[@]}" -gt 1 ]; then
        echo ""
        echo "Multiple ADB devices (${devices[*]}) — skip adb install."
        echo "Deploy with: ./deploy.sh -s SERIAL"
        return
    fi
    echo ""
    echo "=== Device detected — installing ==="
    if adb -s "${devices[0]}" install -r "$SCRIPT_DIR/gateway.apk"; then
        echo "APK installed on ${devices[0]}."
    else
        echo "WARNING: adb install failed (priv-app devices should use ./deploy.sh)"
    fi
}

# ── Main ─────────────────────────────────────────────

main() {
    cd "$SCRIPT_DIR"
    if [ -f "$SCRIPT_DIR/.env.build" ]; then
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/.env.build"
    fi

    echo "=== GSM2Computer Bridge Build ==="
    echo ""

    BUILD_TYPE="debug"
    EXPECT_CERT="${GSM2COMPUTER_EXPECT_CERT:-}"
    while [ $# -gt 0 ]; do
        case "$1" in
            --expect-cert)
                EXPECT_CERT="${2:-}"
                if [ -z "$EXPECT_CERT" ]; then
                    echo "ERROR: --expect-cert needs a SHA-256" >&2
                    exit 1
                fi
                shift 2
                ;;
            --expect-cert=*)
                EXPECT_CERT="${1#*=}"
                if [ -z "$EXPECT_CERT" ]; then
                    echo "ERROR: --expect-cert needs a SHA-256" >&2
                    exit 1
                fi
                shift
                ;;
            release|debug)
                BUILD_TYPE="$1"
                shift
                ;;
            *)
                echo "ERROR: unknown arg: $1" >&2
                echo "Usage: $0 [debug|release] [--expect-cert SHA256]" >&2
                exit 1
                ;;
        esac
    done
    export EXPECT_CERT
    export BUILD_TYPE

    check_java
    check_android_sdk
    check_gradle_wrapper

    build_apk "$BUILD_TYPE"
    build_magisk
    install_to_device

    echo ""
    echo "=== Build complete ==="
    echo "  APK:    $SCRIPT_DIR/gateway.apk"
    echo "  Magisk: $SCRIPT_DIR/gateway-magisk.zip"
    echo ""
    echo "Deploy to device:"
    echo "  1. adb push gateway-magisk.zip /sdcard/"
    echo "     Install via Magisk Manager -> Modules, then reboot"
    echo "     (APK is included in the module as a priv-app)"
    echo "  2. After reboot: open app, grant permissions, set as default phone app"
    echo "  3. Enter hub stream URL in Settings, tap START"
    echo ""
    echo "NOTE: Do NOT also 'adb install' — the Magisk module installs the APK"
    echo "      as a privileged system app with CAPTURE_AUDIO_OUTPUT permission."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
