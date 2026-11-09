#!/system/bin/sh
# phonetunnel-ctl status|start|stop|restart
# stop lasts until start or the next boot (Magisk runs service.sh on boot).

set -u

_here=${0%/*}
# shellcheck disable=SC1091
. "$_here/common.sh"

usage() {
    printf '%s\n' "usage: phonetunnel-ctl status|start|stop|restart" >&2
    exit 2
}

stop_one() {
    _pf=$1
    if _p=$(read_pid "$_pf"); then
        kill "$_p" 2>/dev/null || true
        _n=0
        while [ "$_n" -lt 5 ] && kill -0 "$_p" 2>/dev/null; do
            # Integer sleep: some Android toybox builds reject "sleep 0.2".
            sleep 1
            _n=$((_n + 1))
        done
        if kill -0 "$_p" 2>/dev/null; then
            kill -9 "$_p" 2>/dev/null || true
        fi
    fi
    rm -f "$_pf"
}

cmd_stop() {
    # Supervisor's TERM trap stops the tunnel, dropbear, and the watchdog.
    stop_one "$STATE_DIR/supervisor.pid"
    stop_one "$STATE_DIR/tunnel.pid"
    stop_one "$STATE_DIR/dropbear.pid"
    stop_one "$STATE_DIR/watchdog.pid"
    log_msg "ctl stop"
}

cmd_start() {
    if _p=$(read_pid "$STATE_DIR/supervisor.pid"); then
        printf '%s\n' "already running pid $_p"
        return 0
    fi
    mkdir -p "$STATE_DIR"
    export PHONETUNNEL_MODDIR
    "$PHONETUNNEL_MODDIR/bin/supervisor.sh" >>"$STATE_DIR/service.log" 2>&1 &
    sleep 1
    if _p=$(read_pid "$STATE_DIR/supervisor.pid"); then
        printf '%s\n' "started pid $_p"
        return 0
    fi
    printf '%s\n' "failed to start; see $STATE_DIR/service.log" >&2
    return 1
}

[ $# -eq 1 ] || usage
case "$1" in
    status) exec "$_here/status.sh" ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart)
        cmd_stop
        cmd_start
        ;;
    *) usage ;;
esac
