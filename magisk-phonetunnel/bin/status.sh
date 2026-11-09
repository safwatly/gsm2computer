#!/system/bin/sh
# Human-readable status. Safe to run any time; it does not restart anything.

set -u

_here=${0%/*}
# shellcheck disable=SC1091
. "$_here/common.sh"

_ver=$(grep '^version=' "$PHONETUNNEL_MODDIR/module.prop" 2>/dev/null | cut -d= -f2 || true)

if [ -f "$CONFIG_FILE" ]; then
    if load_config; then
        _cfg=present
    else
        _cfg=invalid
    fi
else
    _cfg=missing
fi

_id=missing
[ -f "$STATE_DIR/id_ed25519" ] && _id=present
_kh=missing
[ -f "$STATE_DIR/home/.ssh/known_hosts" ] && _kh=present

_sshd=down
port_listening "$PHONE_SSH_PORT" && _sshd=listening
_adb=down
port_listening "$PHONE_ADB_PORT" && _adb=listening

_spid=none
_sup=down
if _p=$(read_pid "$STATE_DIR/supervisor.pid"); then
    _spid=$_p
    _sup=up
fi
_tpid=none
_session=down
if _p=$(read_pid "$STATE_DIR/tunnel.pid"); then
    _tpid=$_p
    _session=up
fi
# tunnel=up is the shell path, not merely a live ssh process.
_tun=down
if [ "$_session" = up ] && [ "$_sshd" = listening ]; then
    _tun=up
fi

_fp=unavailable
if [ -s "$STATE_DIR/dropbear_ed25519_host_key" ]; then
    _line=$("$DROPBEARKEY" -y -f "$STATE_DIR/dropbear_ed25519_host_key" 2>/dev/null | awk '/^Fingerprint:/ {print $2; exit}' || true)
    if [ -n "${_line:-}" ]; then
        _fp=$_line
    fi
fi

_ts=$(tailscale_state)
_ts_addr=none
if [ "$_ts" = up ]; then
    _ts_addr=$(${IP_BIN:-ip} addr show tailscale0 2>/dev/null | awk '/inet / {print $2; exit}' || true)
    [ -n "$_ts_addr" ] || _ts_addr=none
fi
_clamped=no
if [ "${PHONETUNNEL_TIMERS_CLAMPED:-0}" = 1 ]; then
    _clamped=yes
fi

_wd=$(cat "$STATE_DIR/watchdog.last_iso" 2>/dev/null || true)
[ -n "$_wd" ] || _wd=never

printf '%s\n' "module=gsm2computer-phonetunnel"
printf '%s\n' "version=${_ver:-unknown}"
printf '%s\n' "state_dir=$STATE_DIR"
printf '%s\n' "log=$LOG_FILE"
printf '%s\n' "config=$_cfg"
printf '%s\n' "cup=${CUP_HOST}:${CUP_PORT}"
printf '%s\n' "cup_user=$CUP_USER"
printf '%s\n' "identity=$_id"
printf '%s\n' "known_hosts=$_kh"
printf '%s\n' "supervisor=$_sup"
printf '%s\n' "supervisor_pid=$_spid"
printf '%s\n' "tunnel=$_tun"
printf '%s\n' "tunnel_session=$_session"
printf '%s\n' "tunnel_pid=$_tpid"
printf '%s\n' "phone_sshd=$_sshd"
printf '%s\n' "phone_sshd_port=127.0.0.1:$PHONE_SSH_PORT"
printf '%s\n' "host_fingerprint=$_fp"
printf '%s\n' "phone_adb=$_adb"
printf '%s\n' "phone_adb_managed=untouched"
printf '%s\n' "phone_adb_port=127.0.0.1:$PHONE_ADB_PORT"
printf '%s\n' "timers_clamped=$_clamped"
printf '%s\n' "cup_ssh_forward=127.0.0.1:$SSH_REMOTE_PORT"
printf '%s\n' "cup_adb_forward=127.0.0.1:$ADB_REMOTE_PORT"
printf '%s\n' "keepalive_sec=$KEEPALIVE_SEC"
printf '%s\n' "tailscale=$_ts"
printf '%s\n' "tailscale_addr=$_ts_addr"
printf '%s\n' "watchdog_last_restart=$_wd"
if [ -f "$LOG_FILE" ]; then
    printf '%s\n' "last_log=$(tail -n 1 "$LOG_FILE" 2>/dev/null || true)"
fi
