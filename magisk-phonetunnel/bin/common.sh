# Shared helpers for the phone tunnel. Sourced, not executed.
# POSIX sh (Android mksh and the Linux stand-in).

: "${PHONETUNNEL_MODDIR:=}"
if [ -z "$PHONETUNNEL_MODDIR" ]; then
    _common_here=${0%/*}
    PHONETUNNEL_MODDIR=${_common_here%/*}
fi

STATE_DIR="${PHONETUNNEL_STATE:-/data/adb/phonetunnel}"
LOG_FILE="${PHONETUNNEL_LOG:-$STATE_DIR/phonetunnel.log}"
CONFIG_FILE="${PHONETUNNEL_CONFIG:-$STATE_DIR/config.env}"

DBCLIENT="${PHONETUNNEL_DBCLIENT:-$PHONETUNNEL_MODDIR/bin/dbclient}"
DROPBEAR="${PHONETUNNEL_DROPBEAR:-$PHONETUNNEL_MODDIR/bin/dropbear}"
DROPBEARKEY="${PHONETUNNEL_DROPBEARKEY:-$PHONETUNNEL_MODDIR/bin/dropbearkey}"

# Production defaults. load_config overrides them from CONFIG_FILE.
CUP_HOST="62.83.35.197"
CUP_PORT="22"
CUP_USER="phonetunnel"
KEEPALIVE_SEC="45"
BACKOFF_INITIAL_SEC="2"
BACKOFF_MAX_SEC="60"
SSH_REMOTE_PORT="2222"
ADB_REMOTE_PORT="5556"
PHONE_SSH_PORT="2222"
PHONE_ADB_PORT="5555"
TS_PACKAGE="com.tailscale.ipn"
TS_ACTIVITY="com.tailscale.ipn/com.tailscale.ipn.IPNActivity"
TS_CHECK_SEC="60"
TS_DOWN_STREAK="3"
TS_RESTART_MIN_SEC="900"

log_msg() {
    _msg="$*"
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    if [ -f "$LOG_FILE" ]; then
        _sz=$(wc -c < "$LOG_FILE" 2>/dev/null | tr -cd '0-9' || true)
        [ -n "$_sz" ] || _sz=0
        if [ -n "$_sz" ] && [ "$_sz" -gt 262144 ]; then
            mv "$LOG_FILE" "${LOG_FILE}.1" 2>/dev/null || true
        fi
    fi
    printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || date)" "$_msg" >> "$LOG_FILE" 2>/dev/null || true
    # Android toolbox logger. Skip the host `log` binary, which is unrelated.
    if command -v getprop >/dev/null 2>&1 && command -v log >/dev/null 2>&1; then
        log -t Phonetunnel "$_msg" 2>/dev/null || true
    fi
}

is_num() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

# Print up, down, or unknown. An ip error is unknown, not down: a
# missing -o flag or a toybox failure must not restart a healthy Tailscale.
tailscale_state() {
    _ip="${IP_BIN:-ip}"
    if ! command -v "$_ip" >/dev/null 2>&1; then
        printf '%s\n' unknown
        return 0
    fi
    _out=$("$_ip" addr show tailscale0 2>&1)
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        if printf '%s\n' "$_out" | grep -E -q -i 'does not exist|Cannot find device|No such device'; then
            printf '%s\n' down
            return 0
        fi
        printf '%s\n' unknown
        return 0
    fi
    if ! printf '%s\n' "$_out" | grep -q 'tailscale0'; then
        printf '%s\n' unknown
        return 0
    fi
    if printf '%s\n' "$_out" | grep -q 'inet '; then
        printf '%s\n' up
        return 0
    fi
    printf '%s\n' down
    return 0
}

# Accept a config value made of a restricted character set.
safe_token() {
    case "$1" in
        ''|*[!A-Za-z0-9._:/@-]*) return 1 ;;
    esac
    return 0
}

load_config() {
    [ -f "$CONFIG_FILE" ] || return 0
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            ''|'#'*) continue ;;
        esac
        _key=${_line%%=*}
        _val=${_line#*=}
        if ! safe_token "$_val"; then
            log_msg "config: rejected $_key (characters)"
            return 1
        fi
        case "$_key" in
            CUP_HOST) CUP_HOST="$_val" ;;
            CUP_PORT) CUP_PORT="$_val" ;;
            CUP_USER) CUP_USER="$_val" ;;
            KEEPALIVE_SEC) KEEPALIVE_SEC="$_val" ;;
            BACKOFF_INITIAL_SEC) BACKOFF_INITIAL_SEC="$_val" ;;
            BACKOFF_MAX_SEC) BACKOFF_MAX_SEC="$_val" ;;
            SSH_REMOTE_PORT) SSH_REMOTE_PORT="$_val" ;;
            ADB_REMOTE_PORT) ADB_REMOTE_PORT="$_val" ;;
            PHONE_SSH_PORT) PHONE_SSH_PORT="$_val" ;;
            PHONE_ADB_PORT) PHONE_ADB_PORT="$_val" ;;
            TS_PACKAGE) TS_PACKAGE="$_val" ;;
            TS_ACTIVITY) TS_ACTIVITY="$_val" ;;
            TS_CHECK_SEC) TS_CHECK_SEC="$_val" ;;
            TS_DOWN_STREAK) TS_DOWN_STREAK="$_val" ;;
            TS_RESTART_MIN_SEC) TS_RESTART_MIN_SEC="$_val" ;;
            *) log_msg "config: ignored $_key" ;;
        esac
    done < "$CONFIG_FILE"

    if ! is_num "$CUP_PORT" || ! is_num "$KEEPALIVE_SEC" \
        || ! is_num "$BACKOFF_INITIAL_SEC" || ! is_num "$BACKOFF_MAX_SEC" \
        || ! is_num "$SSH_REMOTE_PORT" || ! is_num "$ADB_REMOTE_PORT" \
        || ! is_num "$PHONE_SSH_PORT" || ! is_num "$PHONE_ADB_PORT" \
        || ! is_num "$TS_CHECK_SEC" || ! is_num "$TS_DOWN_STREAK" \
        || ! is_num "$TS_RESTART_MIN_SEC"; then
        log_msg "config: a numeric field is not a number"
        return 1
    fi
    # A zero keepalive never notices a dead path. A zero backoff spins.
    # The watchdog interval and restart gap have higher floors: 0 is a
    # busy loop, and a 0 restart gap would force-stop Tailscale on every sample.
    PHONETUNNEL_TIMERS_CLAMPED=0
    if [ "$KEEPALIVE_SEC" -lt 1 ]; then
        log_msg "config: KEEPALIVE_SEC ${KEEPALIVE_SEC} raised to 45"
        KEEPALIVE_SEC=45
        PHONETUNNEL_TIMERS_CLAMPED=1
    fi
    if [ "$BACKOFF_INITIAL_SEC" -lt 1 ]; then
        log_msg "config: BACKOFF_INITIAL_SEC ${BACKOFF_INITIAL_SEC} raised to 1"
        BACKOFF_INITIAL_SEC=1
        PHONETUNNEL_TIMERS_CLAMPED=1
    fi
    if [ "$BACKOFF_MAX_SEC" -lt "$BACKOFF_INITIAL_SEC" ]; then
        BACKOFF_MAX_SEC="$BACKOFF_INITIAL_SEC"
    fi
    if [ "$TS_CHECK_SEC" -lt 15 ]; then
        log_msg "config: TS_CHECK_SEC ${TS_CHECK_SEC} raised to 15"
        TS_CHECK_SEC=15
        PHONETUNNEL_TIMERS_CLAMPED=1
    fi
    if [ "$TS_RESTART_MIN_SEC" -lt 60 ]; then
        log_msg "config: TS_RESTART_MIN_SEC ${TS_RESTART_MIN_SEC} raised to 60"
        TS_RESTART_MIN_SEC=60
        PHONETUNNEL_TIMERS_CLAMPED=1
    fi
    if [ "$TS_DOWN_STREAK" -lt 2 ]; then
        log_msg "config: TS_DOWN_STREAK ${TS_DOWN_STREAK} raised to 2"
        TS_DOWN_STREAK=2
        PHONETUNNEL_TIMERS_CLAMPED=1
    fi
    # The watchdog may only restart Tailscale. A bad config must not
    # force-stop the bridge package.
    case "$TS_PACKAGE" in
        com.tailscale.ipn) ;;
        *)
            log_msg "config: TS_PACKAGE must be com.tailscale.ipn"
            return 1
            ;;
    esac
    case "$TS_ACTIVITY" in
        com.tailscale.ipn/*) ;;
        *)
            log_msg "config: TS_ACTIVITY must start with com.tailscale.ipn/"
            return 1
            ;;
    esac
    return 0
}

# True when something is in TCP listen state on $1.
port_listening() {
    _want=$(printf '%04X' "$1")
    _want_lc=$(printf '%04x' "$1")
    _f=""
    for _f in /proc/net/tcp /proc/net/tcp6; do
        [ -r "$_f" ] || continue
        _line=""
        while IFS= read -r _line; do
            # shellcheck disable=SC2086
            set -- $_line
            [ "$#" -ge 4 ] || continue
            _port=${2##*:}
            case "$_port" in
                [0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]) ;;
                *) continue ;;
            esac
            if [ "$4" = "0A" ] && { [ "$_port" = "$_want" ] || [ "$_port" = "$_want_lc" ]; }; then
                return 0
            fi
        done < "$_f"
    done
    return 1
}

# Dropbear rejects authorized_keys when any parent directory is
# group- or world-writable. /data on Android usually is.
component_perms_ok() {
    _path=$1
    _uid=""
    _mode=""
    if _uid=$(stat -c %u "$_path" 2>/dev/null) && _mode=$(stat -c %a "$_path" 2>/dev/null); then
        :
    else
        _ls=$(ls -ld "$_path" 2>/dev/null) || return 1
        _mode_str=$(printf '%s\n' "$_ls" | awk '{print $1}')
        _owner=$(printf '%s\n' "$_ls" | awk '{print $3}')
        _gw=$(printf '%s' "$_mode_str" | cut -c6)
        _ow=$(printf '%s' "$_mode_str" | cut -c9)
        [ "$_gw" = "w" ] && return 1
        [ "$_ow" = "w" ] && return 1
        _me=$(id -un)
        [ "$_owner" = "$_me" ] || [ "$_owner" = "root" ] || return 1
        return 0
    fi
    _mode=$(printf '%s' "$_mode" | awk '{print substr($0, length($0)-2, 3)}')
    _g=$(printf '%s' "$_mode" | cut -c2)
    _o=$(printf '%s' "$_mode" | cut -c3)
    case "$_g" in
        2|3|6|7) return 1 ;;
    esac
    case "$_o" in
        2|3|6|7) return 1 ;;
    esac
    _me_uid=$(id -u)
    [ "$_uid" = "$_me_uid" ] || [ "$_uid" = "0" ] || return 1
    return 0
}

keydir_perms_ok() {
    _p=$1
    while [ -n "$_p" ]; do
        component_perms_ok "$_p" || return 1
        [ "$_p" = "/" ] && return 0
        _next=${_p%/*}
        [ -z "$_next" ] && _next=/
        # Guard a path that does not shrink (shouldn't happen).
        [ "$_next" = "$_p" ] && return 1
        _p=$_next
    done
    return 1
}

pid_alive() {
    [ -n "${1:-}" ] || return 1
    is_num "$1" || return 1
    kill -0 "$1" 2>/dev/null
}

read_pid() {
    _pf=$1
    [ -f "$_pf" ] || return 1
    _p=$(cat "$_pf" 2>/dev/null || true)
    pid_alive "$_p" || return 1
    printf '%s\n' "$_p"
}
