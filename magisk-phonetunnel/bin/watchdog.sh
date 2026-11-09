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

# 0 only when dumpsys telephony.registry explicitly shows idle.
# Empty output, a dumpsys error, or an unrecognized state is not idle.
call_verified_idle() {
    if ! command -v dumpsys >/dev/null 2>&1; then
        log_msg "watchdog: dumpsys missing; not restarting Tailscale"
        return 1
    fi
    # The dump is written to a file and grepped there. Holding it in a
    # shell variable and passing it to printf hits "Argument list too long"
    # once telephony.registry is a few hundred kilobytes.
    _idle=0
    _dumpf="$STATE_DIR/telephony.dump"
    # mksh exits the script if a redirect cannot create its file.
    if ! mkdir -p "$STATE_DIR" 2>/dev/null; then
        log_msg "watchdog: cannot store telephony dump; not restarting Tailscale"
    else
    : > "$_dumpf"
    if ! dumpsys telephony.registry >"$_dumpf" 2>&1; then
        log_msg "watchdog: dumpsys telephony.registry unusable; not restarting Tailscale"
    elif ! [ -s "$_dumpf" ]; then
        log_msg "watchdog: dumpsys telephony.registry unusable; not restarting Tailscale"
    # Any active, off-hook, or ringing value blocks a restart, even when
    # another line says mCallState=0. Word boundaries keep mRingingCallState=0
    # from matching RINGING. A contradictory dump is not idle.
    elif grep -E -q -i \
        '(^|[^A-Za-z0-9_])(OFFHOOK|RINGING|FG_ACTIVE|BG_ACTIVE|DIALING|ALERTING|INCOMING|WAITING|HOLDING|DISCONNECTING)([^A-Za-z0-9_]|$)|mCallState=[1-9]|mRingingCallState=[1-9]|mForegroundCallState=[1-9]|mBackgroundCallState=[1-9]|PreciseCallState=ACTIVE([^A-Za-z0-9_]|$)' \
        "$_dumpf"; then
        log_msg "watchdog: call active or contradictory; not restarting Tailscale"
    elif grep -E -q 'mCallState=0' "$_dumpf"; then
        _idle=1
    else
        log_msg "watchdog: telephony state is not an explicit idle; not restarting Tailscale"
    fi
    fi
    rm -f "$_dumpf"
    [ "$_idle" -eq 1 ]
}

watchdog_once() {
    if ! load_config; then
        log_msg "watchdog: config invalid"
        return 0
    fi
    if ! call_verified_idle; then
        log_msg "watchdog: call active or unknown; leaving Tailscale alone"
        printf '0\n' > "$STATE_DIR/watchdog.streak"
        return 0
    fi
    _ts_line=$(tailscale_state)
    _ts=${_ts_line%% *}
    if [ "$_ts" = up ]; then
        printf '0\n' > "$STATE_DIR/watchdog.streak"
        return 0
    fi
    if [ "$_ts" != down ]; then
        log_msg "watchdog: tailscale state unknown; not restarting"
        printf '0\n' > "$STATE_DIR/watchdog.streak"
        return 0
    fi
    _streak=$(cat "$STATE_DIR/watchdog.streak" 2>/dev/null || printf '0')
    is_num "$_streak" || _streak=0
    _streak=$((_streak + 1))
    printf '%s\n' "$_streak" > "$STATE_DIR/watchdog.streak"
    if [ -n "$TS_IFACE" ]; then
        log_msg "watchdog: $TS_IFACE has no IPv4 (streak $_streak of $TS_DOWN_STREAK)"
    else
        log_msg "watchdog: no tun/tailscale address in 100.64.0.0/10 (streak $_streak of $TS_DOWN_STREAK)"
    fi
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
    _sleep=60
    if load_config; then
        _sleep=$TS_CHECK_SEC
    fi
    if ! is_num "$_sleep" || [ "$_sleep" -lt 15 ]; then
        _sleep=60
    fi
    sleep "$_sleep"
done
