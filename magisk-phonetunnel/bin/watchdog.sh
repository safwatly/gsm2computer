#!/system/bin/sh
# Restart the Tailscale Android app when its interface has no IPv4
# address for several checks in a row. Rate-limited. Skips the restart
# while a telephony call is off-hook or ringing so a live call is not
# kicked. Never force-stops any package except com.tailscale.ipn.

set -u

_here=${0%/*}
# shellcheck disable=SC1091
. "$_here/common.sh"

once=0
if [ "${1:-}" = "once" ]; then
    once=1
fi

tailscale_up() {
    _ip="${IP_BIN:-ip}"
    _out=$("$_ip" -o addr show tailscale0 2>/dev/null || true)
    printf '%s\n' "$_out" | grep -q 'inet '
}

# 0 = a call is active (or we cannot tell on a real phone) -> do not restart.
# 1 = idle.
call_active() {
    if ! command -v dumpsys >/dev/null 2>&1; then
        if command -v getprop >/dev/null 2>&1; then
            log_msg "watchdog: dumpsys missing; not restarting Tailscale"
            return 0
        fi
        return 1
    fi
    _dump=$(dumpsys telephony.registry 2>/dev/null || true)
    if printf '%s\n' "$_dump" | grep -E 'mCallState=[12]' >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

watchdog_once() {
    if ! load_config; then
        log_msg "watchdog: config invalid"
        return 0
    fi
    if call_active; then
        log_msg "watchdog: call active or unknown; leaving Tailscale alone"
        printf '0\n' > "$STATE_DIR/watchdog.streak"
        return 0
    fi
    if tailscale_up; then
        printf '0\n' > "$STATE_DIR/watchdog.streak"
        return 0
    fi
    _streak=$(cat "$STATE_DIR/watchdog.streak" 2>/dev/null || printf '0')
    is_num "$_streak" || _streak=0
    _streak=$((_streak + 1))
    printf '%s\n' "$_streak" > "$STATE_DIR/watchdog.streak"
    log_msg "watchdog: tailscale0 has no IPv4 (streak $_streak of $TS_DOWN_STREAK)"
    if [ "$_streak" -lt "$TS_DOWN_STREAK" ]; then
        return 0
    fi
    _now=$(date +%s)
    _last=$(cat "$STATE_DIR/watchdog.last" 2>/dev/null || printf '0')
    is_num "$_last" || _last=0
    _elapsed=$((_now - _last))
    if [ "$_last" -ne 0 ] && [ "$_elapsed" -lt "$TS_RESTART_MIN_SEC" ]; then
        log_msg "watchdog: rate limit ${_elapsed}s < ${TS_RESTART_MIN_SEC}s"
        return 0
    fi
    if ! command -v am >/dev/null 2>&1; then
        log_msg "watchdog: am not found; cannot restart Tailscale"
        return 0
    fi
    log_msg "watchdog: restarting $TS_PACKAGE"
    am force-stop "$TS_PACKAGE" >>"$LOG_FILE" 2>&1 || true
    sleep "${PHONETUNNEL_WATCHDOG_SLEEP:-2}"
    am start -n "$TS_ACTIVITY" >>"$LOG_FILE" 2>&1 \
        || am start -a android.intent.action.MAIN -p "$TS_PACKAGE" >>"$LOG_FILE" 2>&1 \
        || true
    printf '%s\n' "$_now" > "$STATE_DIR/watchdog.last"
    printf '0\n' > "$STATE_DIR/watchdog.streak"
    date '+%Y-%m-%dT%H:%M:%S%z' > "$STATE_DIR/watchdog.last_iso" 2>/dev/null || true
}

mkdir -p "$STATE_DIR"

if [ "$once" -eq 1 ]; then
    watchdog_once
    exit 0
fi

log_msg "watchdog loop start"
while true; do
    watchdog_once || true
    load_config || true
    sleep "$TS_CHECK_SEC"
done
