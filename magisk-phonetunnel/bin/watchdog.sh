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
    else
        # Only the current per-subscription fields count. An idle Moto dump
        # still contains "Ringing call state: -1" and data-connection history
        # ("state: DISCONNECTING") plus older OFFHOOK/RINGING log lines.
        # Those are not a live call. A value on mCallState, the three
        # m*CallState fields, or mPreciseCallState is. -1, 0, and precise
        # DISCONNECTED (7) are idle. Anything else, or no mCallState at all,
        # is not idle.
        _verdict=$(awk '
function trim(v) {
    sub(/^[ \t]+/, "", v)
    sub(/[ \t]+$/, "", v)
    return v
}
function first_token(v) {
    v = trim(v)
    sub(/[, \t].*/, "", v)
    return v
}
function callstate_idle(v) {
    v = first_token(v)
    return (v == "0" || v == "IDLE")
}
function substate_idle(v) {
    v = first_token(v)
    return (v == "0" || v == "-1" || v == "IDLE" || v == "NOT_VALID")
}
function precise_num_idle(v) {
    v = first_token(v)
    if (v == "-1" || v == "0" || v == "7") return 1
    if (v == "IDLE" || v == "NOT_VALID" || v == "DISCONNECTED") return 1
    return 0
}
function value_after(s, label, i, rest, n) {
    i = index(s, label)
    if (i == 0) return ""
    rest = substr(s, i + length(label))
    sub(/^[ \t]+/, "", rest)
    n = index(rest, ",")
    if (n > 0) rest = substr(rest, 1, n - 1)
    sub(/[ \t]+$/, "", rest)
    return rest
}
function consider_precise(v, r, f, b) {
    if (index(v, "Ringing call state:") > 0) {
        r = value_after(v, "Ringing call state:")
        f = value_after(v, "Foreground call state:")
        b = value_after(v, "Background call state:")
        if (!precise_num_idle(r) || !precise_num_idle(f) || !precise_num_idle(b)) bad = 1
        return
    }
    v = trim(v)
    if (!(v == "IDLE" || v == "NOT_VALID" || v == "-1" || v == "0")) bad = 1
}
function take(line, key,    v) {
    matched = 0
    if (index(line, key) != 1) return ""
    matched = 1
    v = substr(line, length(key) + 1)
    sub(/\r$/, "", v)
    return v
}
{
    line = $0
    sub(/\r$/, "", line)
    sub(/^[ \t]+/, "", line)
    v = take(line, "mForegroundCallState=")
    if (matched) { if (!substate_idle(v)) bad = 1; next }
    v = take(line, "mBackgroundCallState=")
    if (matched) { if (!substate_idle(v)) bad = 1; next }
    v = take(line, "mRingingCallState=")
    if (matched) { if (!substate_idle(v)) bad = 1; next }
    v = take(line, "mPreciseCallState=")
    if (matched) { consider_precise(v); next }
    v = take(line, "PreciseCallState=")
    if (matched) { consider_precise(v); next }
    v = take(line, "mCallState=")
    if (matched) {
        seen = 1
        if (!callstate_idle(v)) bad = 1
    }
}
END {
    if (bad) print "active"
    else if (seen) print "idle"
    else print "unknown"
}
' "$_dumpf" 2>/dev/null || true)
        case ${_verdict:-unknown} in
            idle) _idle=1 ;;
            active)
                log_msg "watchdog: call active or contradictory; not restarting Tailscale"
                ;;
            *)
                log_msg "watchdog: telephony state is not an explicit idle; not restarting Tailscale"
                ;;
        esac
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
