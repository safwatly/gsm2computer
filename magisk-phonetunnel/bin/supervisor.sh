#!/system/bin/sh
# Keeps dropbear on localhost and one outbound reverse tunnel to cup.
# Exits only when killed. Restarts the tunnel with backoff when the
# network path dies (Wi-Fi <-> mobile, cup reboot, carrier NAT change).

set -u

export PATH="/system/bin:/system/xbin:/vendor/bin:${PATH:-/usr/bin:/bin}"

_here=${0%/*}
# shellcheck disable=SC1091
. "$_here/common.sh"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

if _old=$(read_pid "$STATE_DIR/supervisor.pid"); then
    log_msg "supervisor already running pid $_old"
    exit 0
fi
printf '%s\n' "$$" > "$STATE_DIR/supervisor.pid"

renice 10 "$$" >/dev/null 2>&1 || renice -n 10 -p "$$" >/dev/null 2>&1 || true

log_msg "supervisor start pid $$ module=$PHONETUNNEL_MODDIR"

# Watchdog is independent of the tunnel so a dead cup does not
# stop Tailscale checks, and a Tailscale restart does not kill ssh.
# Invoke via `sh` so the Android shebang is not required on a
# Linux stand-in. On the phone, `sh` is /system/bin/sh.
sh "$_here/watchdog.sh" >>"$LOG_FILE" 2>&1 &
printf '%s\n' "$!" > "$STATE_DIR/watchdog.pid"

cleanup_supervisor() {
    trap - INT TERM
    log_msg "supervisor stopping"
    if _tp=$(read_pid "$STATE_DIR/tunnel.pid"); then
        kill "$_tp" 2>/dev/null || true
    fi
    if _dp=$(read_pid "$STATE_DIR/dropbear.pid"); then
        kill "$_dp" 2>/dev/null || true
    fi
    if _wp=$(read_pid "$STATE_DIR/watchdog.pid"); then
        kill "$_wp" 2>/dev/null || true
    fi
    rm -f "$STATE_DIR/supervisor.pid" "$STATE_DIR/tunnel.pid"
    exit 0
}
trap cleanup_supervisor INT TERM

wait_for_route() {
    _i=0
    while [ "$_i" -lt 30 ]; do
        if awk 'NR>1 && $2=="00000000" {found=1} END{exit !found}' /proc/net/route 2>/dev/null; then
            return 0
        fi
        _i=$((_i + 1))
        sleep 2
    done
    log_msg "no IPv4 default route yet; connecting anyway"
}

prepare_keydir() {
    _cand=""
    _list="$STATE_DIR/sshd"
    if command -v getprop >/dev/null 2>&1; then
        # /data is group-writable on Android, so prefer a /dev path.
        _list="/dev/phonetunnel-keys $STATE_DIR/sshd"
    else
        _list="$STATE_DIR/sshd ${HOME:-}/.phonetunnel-keys /dev/phonetunnel-keys"
    fi
    for _cand in $_list; do
        [ -n "$_cand" ] || continue
        mkdir -p "$_cand" 2>/dev/null || continue
        chmod 755 "$_cand" 2>/dev/null || true
        if keydir_perms_ok "$_cand"; then
            printf '%s\n' "$_cand"
            return 0
        fi
    done
    return 1
}

refresh_authorized_keys() {
    _dir=$1
    cp "$STATE_DIR/authorized_keys" "$_dir/authorized_keys"
    chmod 600 "$_dir/authorized_keys" 2>/dev/null || true
}

start_phone_sshd() {
    if [ ! -f "$STATE_DIR/authorized_keys" ]; then
        log_msg "phone sshd waiting for $STATE_DIR/authorized_keys"
        return 1
    fi
    if port_listening "$PHONE_SSH_PORT" && [ -f "$STATE_DIR/keydir.path" ]; then
        _existing=$(cat "$STATE_DIR/keydir.path")
        if [ -d "$_existing" ]; then
            refresh_authorized_keys "$_existing"
        fi
        return 0
    fi
    # The listener is gone. Drop a wedged pid so the next start can bind.
    if _p=$(read_pid "$STATE_DIR/dropbear.pid"); then
        kill "$_p" 2>/dev/null || true
    fi
    rm -f "$STATE_DIR/dropbear.pid"
    if [ ! -s "$STATE_DIR/dropbear_ed25519_host_key" ]; then
        log_msg "generating dropbear host key"
        if ! "$DROPBEARKEY" -t ed25519 -f "$STATE_DIR/dropbear_ed25519_host_key" >>"$LOG_FILE" 2>&1; then
            log_msg "dropbearkey failed"
            return 1
        fi
        chmod 600 "$STATE_DIR/dropbear_ed25519_host_key" 2>/dev/null || true
    fi
    _keydir=$(prepare_keydir) || {
        log_msg "no directory with dropbear-safe permissions for authorized_keys"
        return 1
    }
    printf '%s\n' "$_keydir" > "$STATE_DIR/keydir.path"
    refresh_authorized_keys "$_keydir"
    log_msg "starting dropbear on 127.0.0.1:$PHONE_SSH_PORT keydir=$_keydir"
    # -F stays in the foreground; we background it. -j/-k disable
    # forwarding so this sshd is a shell only. Password auth is
    # compiled out. Stderr is the log (syslog is compiled out).
    "$DROPBEAR" -F -p "127.0.0.1:$PHONE_SSH_PORT" \
        -r "$STATE_DIR/dropbear_ed25519_host_key" \
        -D "$_keydir" \
        -j -k \
        -P "$STATE_DIR/dropbear.pid" \
        >>"$LOG_FILE" 2>&1 &
    _n=0
    while [ "$_n" -lt 8 ]; do
        if port_listening "$PHONE_SSH_PORT"; then
            return 0
        fi
        sleep 1
        _n=$((_n + 1))
    done
    log_msg "dropbear did not listen on $PHONE_SSH_PORT"
    return 1
}

keys_ready() {
    [ -f "$STATE_DIR/id_ed25519" ] && [ -f "$STATE_DIR/home/.ssh/known_hosts" ]
}

connect_once() {
    mkdir -p "$STATE_DIR/home/.ssh"
    chmod 700 "$STATE_DIR/home" "$STATE_DIR/home/.ssh" 2>/dev/null || true
    chmod 600 "$STATE_DIR/id_ed25519" 2>/dev/null || true
    log_msg "connecting ${CUP_USER}@${CUP_HOST}:${CUP_PORT} keepalive=${KEEPALIVE_SEC}s"
    # HOME selects dbclient's known_hosts file. -N -T: no command, no pty.
    # ExitOnForwardFailure: a taken or forbidden remote port exits so we retry.
    HOME="$STATE_DIR/home" \
        "$DBCLIENT" -N -T \
        -i "$STATE_DIR/id_ed25519" \
        -l "$CUP_USER" \
        -p "$CUP_PORT" \
        -o BatchMode=yes \
        -o ExitOnForwardFailure=yes \
        -o StrictHostKeyChecking=yes \
        -o "ServerAliveInterval=$KEEPALIVE_SEC" \
        -R "127.0.0.1:${SSH_REMOTE_PORT}:127.0.0.1:${PHONE_SSH_PORT}" \
        -R "127.0.0.1:${ADB_REMOTE_PORT}:127.0.0.1:${PHONE_ADB_PORT}" \
        "$CUP_HOST" >>"$LOG_FILE" 2>&1 &
    _child=$!
    printf '%s\n' "$_child" > "$STATE_DIR/tunnel.pid"
    # dbclient can stay up after dropbear dies. Restart the sshd in
    # place so the reverse shell port recovers without a new login.
    while kill -0 "$_child" 2>/dev/null; do
        if ! port_listening "$PHONE_SSH_PORT"; then
            log_msg "phone sshd not listening; restarting it"
            start_phone_sshd || log_msg "phone sshd restart failed"
        fi
        sleep 2
    done
    wait "$_child"
    _rc=$?
    rm -f "$STATE_DIR/tunnel.pid"
    return "$_rc"
}

wait_for_route

backoff=""
while true; do
    if ! load_config; then
        log_msg "config invalid; retry in 30s"
        sleep 30
        continue
    fi
    if [ -z "$backoff" ]; then
        backoff="$BACKOFF_INITIAL_SEC"
    fi
    if ! keys_ready; then
        log_msg "waiting for id_ed25519 and home/.ssh/known_hosts in $STATE_DIR"
        sleep 30
        continue
    fi
    # dbclient's OpenSSH private-key loader misreads the cipher header
    # and exits with "String too long". Require a dropbearkey file.
    if grep -q 'OPENSSH PRIVATE KEY' "$STATE_DIR/id_ed25519" 2>/dev/null; then
        log_msg "id_ed25519 is an OpenSSH private key. Generate it with dropbearkey (setup script does this)."
        sleep 30
        continue
    fi
    # Do not open the tunnel until the phone sshd is actually listening.
    # adbd is not started or reconfigured: stock adbd would listen on
    # every interface, and a persisted tcp port would survive removal.
    if ! start_phone_sshd; then
        log_msg "phone sshd is down; not opening the tunnel"
        sleep "$backoff"
        _next=$((backoff * 2))
        if [ "$_next" -gt "$BACKOFF_MAX_SEC" ]; then
            backoff="$BACKOFF_MAX_SEC"
        else
            backoff="$_next"
        fi
        continue
    fi
    if port_listening "$PHONE_ADB_PORT"; then
        log_msg "phone adb port $PHONE_ADB_PORT is already listening; forwarding it"
    else
        log_msg "phone adb port $PHONE_ADB_PORT is not listening; the adb forward will have nothing behind it"
    fi
    _start=$(date +%s)
    connect_once
    _rc=$?
    _now=$(date +%s)
    _up=$((_now - _start))
    log_msg "tunnel exited rc=$_rc uptime=${_up}s next_sleep=${backoff}s"
    # A session that stayed up has proven the path. Reset backoff so a
    # later blip reconnects quickly. A fast failure keeps backing off.
    if [ "$_up" -ge 60 ]; then
        backoff="$BACKOFF_INITIAL_SEC"
    fi
    sleep "$backoff"
    _next=$((backoff * 2))
    if [ "$_next" -gt "$BACKOFF_MAX_SEC" ]; then
        backoff="$BACKOFF_MAX_SEC"
    else
        backoff="$_next"
    fi
done
