#!/bin/bash
# Stand-in harness for the phone tunnel. This is not the Moto and not cup.
# Subcommands: doctor, launch, drive-tunnel, drive-all, cleanup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="${PHONETUNNEL_REPO:-$(cd "$SCRIPT_DIR/../../../.." && pwd)}"
EVIDENCE="${PHONETUNNEL_EVIDENCE:-/opt/cursor/artifacts/verify-phonetunnel}"
RUNFILE="${PHONETUNNEL_RUNFILE:-/tmp/verify-phonetunnel-run}"
MODULE="$REPO/magisk-phonetunnel"
SETUP="$REPO/scripts/setup-phonetunnel-account.sh"
MULTI="$MODULE/bin/dropbearmulti"

mkdir -p "$EVIDENCE"

die() {
    printf 'harness: %s\n' "$*" >&2
    exit 1
}

run_dir() {
    [ -f "$RUNFILE" ] || die "stand-in is not running (no $RUNFILE). Run launch first."
    cat "$RUNFILE"
}

free_port() {
    python3 - << 'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

tcp_open() {
    python3 - "$1" << 'PY'
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except Exception:
    sys.exit(1)
finally:
    s.close()
PY
}

cmd_doctor() {
    local out="$EVIDENCE/doctor.txt"
    {
        echo "doctor $(date -Is)"
        echo "repo=$REPO"
        [ -f "$MULTI" ] || die "missing $MULTI"
        file "$MULTI"
        file "$MULTI" | grep -q 'ARM aarch64' || die "binary is not aarch64"
        file "$MULTI" | grep -q 'static-pie' || die "binary is not static-pie"
        command -v qemu-aarch64-static >/dev/null || die "qemu-aarch64-static missing"
        qemu-aarch64-static "$MULTI" dbclient -h >/dev/null 2>&1
        echo "qemu dbclient -h: ok"
        command -v ssh >/dev/null || die "ssh client missing"
        [ -x /usr/sbin/sshd ] || die "/usr/sbin/sshd missing"
        ssh -V 2>&1
        echo "sshd: /usr/sbin/sshd"

        local scratch before after
        scratch=$(mktemp -d)
        mkdir -p "$scratch/etc/ssh"
        printf 'Port 1\n' > "$scratch/etc/ssh/sshd_config"
        before=$(sha256sum "$scratch/etc/ssh/sshd_config")
        "$SETUP" --dry-run --root "$scratch" --cup-host 127.0.0.1 > "$scratch/dry.txt"
        after=$(sha256sum "$scratch/etc/ssh/sshd_config")
        echo "dry-run output:"
        cat "$scratch/dry.txt"
        [ "$before" = "$after" ] || die "dry-run modified sshd_config"
        [ ! -e "$scratch/root/phonetunnel-install" ] || die "dry-run created a key bundle"
        echo "dry-run left sshd_config unchanged and created no keys"
        rm -rf "$scratch"

        if [ -f "$RUNFILE" ]; then
            local run
            run=$(cat "$RUNFILE")
            echo "stand-in run dir: $run"
            if [ -f "$run/state/config.env" ]; then
                PHONETUNNEL_STATE="$run/state" \
                    PHONETUNNEL_MODDIR="$MODULE" \
                    PHONETUNNEL_CONFIG="$run/state/config.env" \
                    sh "$MODULE/bin/status.sh" || true
            fi
        else
            echo "stand-in: not launched"
        fi
        echo "doctor ok"
    } | tee "$out"
}

save_pid() {
    printf '%s\n' "$1" >> "$(run_dir)/pids"
}

cmd_launch() {
    if [ -f "$RUNFILE" ]; then
        echo "replacing previous stand-in"
        cmd_cleanup || true
    fi
    local run
    run=$(mktemp -d /tmp/phonetunnel-standin.XXXXXX)
    printf '%s\n' "$run" > "$RUNFILE"
    chmod 700 "$run"
    : > "$run/pids"

    local system_sum
    system_sum=$(sha256sum /etc/ssh/sshd_config)
    printf '%s\n' "$system_sum" > "$run/system-sshd.sha"

    if ! id phonetunnel >/dev/null 2>&1; then
        sudo useradd --system --create-home --shell /usr/sbin/nologin --home-dir /home/phonetunnel phonetunnel
        echo "created" > "$run/user-created"
    fi
    # OpenSSH refuses pubkey auth when the shadow field starts with '!'.
    sudo usermod -p '*' phonetunnel
    sudo usermod -s /usr/sbin/nologin phonetunnel

    local cup_port ssh_remote adb_remote phone_ssh phone_adb
    cup_port=$(free_port)
    ssh_remote=$(free_port)
    adb_remote=$(free_port)
    phone_ssh=$(free_port)
    phone_adb=$(free_port)
    cat > "$run/ports" << EOF
CUP_PORT=$cup_port
SSH_REMOTE=$ssh_remote
ADB_REMOTE=$adb_remote
PHONE_SSH=$phone_ssh
PHONE_ADB=$phone_adb
EOF

    local prefix="$run/cuproot"
    mkdir -p "$prefix/etc/ssh"
    ssh-keygen -t ed25519 -f "$prefix/etc/ssh/ssh_host_ed25519_key" -N "" -C standin-cup >/dev/null
    chmod 600 "$prefix/etc/ssh/ssh_host_ed25519_key"

    cat > "$prefix/etc/ssh/sshd_config" << EOF
Port ${cup_port}
ListenAddress 127.0.0.1
HostKey ${prefix}/etc/ssh/ssh_host_ed25519_key
PidFile ${run}/sshd.pid
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
# The account's real home, not a path under /tmp. sshd opens this file
# as the phonetunnel user and StrictModes rejects a parent owned by the
# harness user or a world-writable directory such as /tmp.
AuthorizedKeysFile /home/phonetunnel/.ssh/authorized_keys
ClientAliveInterval 4
ClientAliveCountMax 2
EOF

    sudo "$SETUP" \
        --root "$prefix" \
        --cup-host 127.0.0.1 \
        --cup-port "$cup_port" \
        --ssh-port "$ssh_remote" \
        --adb-port "$adb_remote" \
        --phone-ssh-port "$phone_ssh" \
        --phone-adb-port "$phone_adb" \
        --keepalive 2 \
        --backoff-initial 1 \
        --backoff-max 4 \
        > "$run/setup.out"

    # Install the cup authorized_keys into the real account home so
    # OpenSSH StrictModes accepts it. The prefix copy stays as the
    # bundle the setup script printed.
    sudo install -d -m 700 -o phonetunnel -g phonetunnel /home/phonetunnel/.ssh
    if [ -f /home/phonetunnel/.ssh/authorized_keys ]; then
        sudo cp -a /home/phonetunnel/.ssh/authorized_keys "$run/authorized_keys.before"
    fi
    sudo cp "$prefix/home/phonetunnel/.ssh/authorized_keys" /home/phonetunnel/.ssh/authorized_keys
    sudo chown phonetunnel:phonetunnel /home/phonetunnel/.ssh/authorized_keys
    sudo chmod 600 /home/phonetunnel/.ssh/authorized_keys
    sudo chmod 750 /home/phonetunnel
    : > "$run/real-home-keys"

    sudo mkdir -p /run/sshd
    sudo /usr/sbin/sshd -f "$prefix/etc/ssh/sshd_config"
    sleep 0.3
    [ -f "$run/sshd.pid" ] || die "sshd did not write a pid file"

    local bundle="$prefix/root/phonetunnel-install"
    local state="$run/state"
    mkdir -p "$state/home/.ssh"
    sudo cp "$bundle/id_ed25519" "$state/id_ed25519"
    sudo cp "$bundle/known_hosts" "$state/home/.ssh/known_hosts"
    sudo cp "$bundle/authorized_keys" "$state/authorized_keys"
    sudo cp "$bundle/config.env" "$state/config.env"
    sudo cp "$bundle/phone_shell_ed25519" "$run/phone_shell_ed25519"
    sudo chown -R "$(id -u):$(id -g)" "$state" "$run/phone_shell_ed25519"
    chmod 700 "$state" "$state/home" "$state/home/.ssh"
    chmod 600 "$state/id_ed25519" "$state/authorized_keys" "$state/config.env" "$run/phone_shell_ed25519"
    chmod 644 "$state/home/.ssh/known_hosts"

    cat > "$run/adb-standin.py" << 'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((host, port))
s.listen(8)
while True:
    c, _ = s.accept()
    data = b""
    c.settimeout(2)
    try:
        while b"\n" not in data and len(data) < 128:
            chunk = c.recv(128)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    if data.strip() == b"adb-probe":
        c.sendall(b"adb-standin-ok\n")
    else:
        c.sendall(b"adb-standin-unexpected\n")
    c.close()
PY
    python3 "$run/adb-standin.py" 127.0.0.1 "$phone_adb" > "$run/adb-standin.log" 2>&1 &
    echo $! >> "$run/pids"
    sleep 0.2
    tcp_open "$phone_adb" || die "adb stand-in did not listen"

    mkdir -p "$run/bin"
    cat > "$run/bin/dbclient" << EOF
#!/bin/sh
exec qemu-aarch64-static "$MULTI" dbclient "\$@"
EOF
    cat > "$run/bin/dropbear" << EOF
#!/bin/sh
exec qemu-aarch64-static "$MULTI" dropbear "\$@"
EOF
    cat > "$run/bin/dropbearkey" << EOF
#!/bin/sh
exec qemu-aarch64-static "$MULTI" dropbearkey "\$@"
EOF
    chmod 755 "$run/bin/dbclient" "$run/bin/dropbear" "$run/bin/dropbearkey"

    PHONETUNNEL_STATE="$state" \
        PHONETUNNEL_MODDIR="$MODULE" \
        PHONETUNNEL_CONFIG="$state/config.env" \
        PHONETUNNEL_LOG="$state/phonetunnel.log" \
        PHONETUNNEL_DBCLIENT="$run/bin/dbclient" \
        PHONETUNNEL_DROPBEAR="$run/bin/dropbear" \
        PHONETUNNEL_DROPBEARKEY="$run/bin/dropbearkey" \
        sh "$MODULE/bin/supervisor.sh" >> "$state/service.log" 2>&1 &
    echo $! >> "$run/pids"

    local i
    for i in $(seq 1 40); do
        if tcp_open "$ssh_remote"; then
            break
        fi
        sleep 1
    done
    tcp_open "$ssh_remote" || {
        echo "---- phonetunnel.log ----" >&2
        cat "$state/phonetunnel.log" >&2 || true
        echo "---- service.log ----" >&2
        cat "$state/service.log" >&2 || true
        die "reverse ssh port $ssh_remote did not open"
    }

    local nonce
    nonce="launch-$(date +%s)"
    ssh -i "$run/phone_shell_ed25519" \
        -p "$ssh_remote" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=accept-new \
        -o UserKnownHostsFile="$run/phone_known_hosts" \
        -o BatchMode=yes \
        -o ConnectTimeout=10 \
        "$(id -un)@127.0.0.1" "printf '%s\n' '$nonce'" > "$run/launch.echo"
    grep -q "$nonce" "$run/launch.echo" || die "launch shell did not echo the nonce"

    {
        echo "launch ok $(date -Is)"
        echo "run=$run"
        cat "$run/ports"
        echo "nonce=$nonce"
        echo "system sshd_config unchanged check recorded"
    } | tee "$EVIDENCE/launch.txt"
}

shell_echo() {
    local run nonce
    run=$(run_dir)
    # shellcheck disable=SC1091
    . "$run/ports"
    nonce=$1
    ssh -i "$run/phone_shell_ed25519" \
        -p "$SSH_REMOTE" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$run/phone_known_hosts" \
        -o BatchMode=yes \
        -o ConnectTimeout=10 \
        "$(id -un)@127.0.0.1" "printf '%s\n' '$nonce'"
}

cmd_drive_tunnel() {
    local run out
    run=$(run_dir)
    # shellcheck disable=SC1091
    . "$run/ports"
    out="$EVIDENCE/tunnel-up.txt"
    {
        echo "drive tunnel-up $(date -Is)"
        PHONETUNNEL_STATE="$run/state" \
            PHONETUNNEL_MODDIR="$MODULE" \
            PHONETUNNEL_CONFIG="$run/state/config.env" \
            sh "$MODULE/bin/status.sh"
        grep -q '^tunnel=up$' <(PHONETUNNEL_STATE="$run/state" \
            PHONETUNNEL_MODDIR="$MODULE" \
            PHONETUNNEL_CONFIG="$run/state/config.env" \
            sh "$MODULE/bin/status.sh")

        local nonce
        nonce="tunnel-$(date +%s)-$$"
        shell_echo "$nonce" | tee "$run/tunnel.echo"
        grep -q "$nonce" "$run/tunnel.echo"

        # A reverse forward to any other port must be refused.
        set +e
        HOME="$run/state/home" "$run/bin/dbclient" -N -T \
            -i "$run/state/id_ed25519" \
            -l phonetunnel \
            -p "$CUP_PORT" \
            -o BatchMode=yes \
            -o ExitOnForwardFailure=yes \
            -o StrictHostKeyChecking=yes \
            -R "127.0.0.1:39999:127.0.0.1:9" \
            127.0.0.1 > "$run/bad-forward.log" 2>&1
        local bad_rc=$?
        set -e
        echo "bad_forward_rc=$bad_rc"
        cat "$run/bad-forward.log"
        [ "$bad_rc" -ne 0 ]

        # A local forward is refused when something tries to use it.
        local lport
        lport=$(free_port)
        HOME="$run/state/home" "$run/bin/dbclient" -N -T \
            -i "$run/state/id_ed25519" \
            -l phonetunnel \
            -p "$CUP_PORT" \
            -o BatchMode=yes \
            -o ExitOnForwardFailure=yes \
            -o StrictHostKeyChecking=yes \
            -L "127.0.0.1:${lport}:127.0.0.1:9" \
            127.0.0.1 > "$run/local-forward.log" 2>&1 &
        local lpid=$!
        sleep 1
        set +e
        python3 - "$lport" << 'PY' > "$run/local-forward.probe" 2>&1
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    s.sendall(b"hi\n")
    data = s.recv(64)
    sys.stdout.write(repr(data))
except Exception as e:
    sys.stdout.write(type(e).__name__ + ": " + str(e))
finally:
    s.close()
PY
        set -e
        kill "$lpid" 2>/dev/null || true
        wait "$lpid" 2>/dev/null || true
        echo "local forward probe:"
        cat "$run/local-forward.probe"
        echo "local forward client log:"
        cat "$run/local-forward.log"
        grep -E -q 'administratively prohibited|Connection reset|ConnectionReset|refused' \
            "$run/local-forward.log" "$run/local-forward.probe"
        echo "tunnel-up ok"
    } | tee "$out"
}

cmd_drive_shell() {
    local run nonce
    run=$(run_dir)
    nonce="shell-$(date +%s)-$$"
    {
        echo "drive phone-shell $(date -Is)"
        shell_echo "$nonce"
        echo "phone-shell ok"
    } | tee "$EVIDENCE/phone-shell.txt"
    grep -q "$nonce" "$EVIDENCE/phone-shell.txt"
}

cmd_drive_adb() {
    local run
    run=$(run_dir)
    # shellcheck disable=SC1091
    . "$run/ports"
    python3 - "$ADB_REMOTE" << 'PY' | tee "$EVIDENCE/adb-forward.txt"
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
s.settimeout(3)
s.connect(("127.0.0.1", port))
s.sendall(b"adb-probe\n")
data = b""
while b"\n" not in data:
    chunk = s.recv(64)
    if not chunk:
        break
    data += chunk
s.close()
text = data.decode()
sys.stdout.write("adb_reply=" + text)
if text.strip() != "adb-standin-ok":
    sys.exit(1)
print("adb-forward ok")
PY
}

wait_shell() {
    local nonce i
    nonce=$1
    for i in $(seq 1 30); do
        if shell_echo "$nonce" 2>/dev/null | grep -q "$nonce"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

cmd_drive_reconnect() {
    local run
    run=$(run_dir)
    # shellcheck disable=SC1091
    . "$run/ports"
    local out="$EVIDENCE/reconnect.txt"
    {
        echo "drive reconnect $(date -Is)"
        local old new nonce
        old=$(cat "$run/state/tunnel.pid")
        echo "kill client pid $old"
        kill "$old"
        new=""
        local i
        for i in $(seq 1 20); do
            if [ -f "$run/state/tunnel.pid" ]; then
                new=$(cat "$run/state/tunnel.pid" || true)
                if [ -n "$new" ] && [ "$new" != "$old" ]; then
                    break
                fi
            fi
            sleep 1
        done
        echo "new client pid ${new:-none}"
        [ -n "$new" ] && [ "$new" != "$old" ]
        nonce="rekill-$(date +%s)"
        wait_shell "$nonce"
        echo "after kill: shell ok ($nonce)"

        if command -v iptables >/dev/null 2>&1; then
            old=$(cat "$run/state/tunnel.pid")
            echo "iptables DROP output to 127.0.0.1:$CUP_PORT (was pid $old)"
            sudo iptables -I OUTPUT -p tcp -d 127.0.0.1 --dport "$CUP_PORT" -j DROP
            echo "$CUP_PORT" > "$run/iptables-dport"
            new=""
            for i in $(seq 1 25); do
                if [ ! -f "$run/state/tunnel.pid" ] || [ "$(cat "$run/state/tunnel.pid" 2>/dev/null || true)" != "$old" ]; then
                    new=$(cat "$run/state/tunnel.pid" 2>/dev/null || true)
                    break
                fi
                sleep 1
            done
            echo "after drop, tunnel pid ${new:-dead} (old $old)"
            sudo iptables -D OUTPUT -p tcp -d 127.0.0.1 --dport "$CUP_PORT" -j DROP
            rm -f "$run/iptables-dport"
            [ "$new" != "$old" ] || [ ! -f "$run/state/tunnel.pid" ]
            nonce="redrop-$(date +%s)"
            wait_shell "$nonce"
            echo "after network drop: shell ok ($nonce)"
        else
            echo "iptables missing; network-drop not driven"
            exit 1
        fi

        old=$(cat "$run/sshd.pid")
        echo "kill stand-in sshd pid $old"
        sudo kill "$old"
        for i in $(seq 1 15); do
            if ! kill -0 "$old" 2>/dev/null; then
                break
            fi
            sleep 0.2
        done
        sudo /usr/sbin/sshd -f "$run/cuproot/etc/ssh/sshd_config"
        nonce="resshd-$(date +%s)"
        wait_shell "$nonce"
        echo "after sshd restart: shell ok ($nonce)"
        echo "reconnect ok"
    } | tee "$out"
}

cmd_drive_watchdog() {
    local run wd
    run=$(run_dir)
    wd="$run/watchdog"
    mkdir -p "$wd/bin" "$wd/state"
    cat > "$wd/bin/ip" << EOF
#!/bin/sh
if [ "\${1:-}" = "-o" ] && [ "\${2:-}" = "addr" ]; then
    if [ -f "$wd/tailscale_up" ]; then
        echo "1: tailscale0    inet 100.64.0.2/32 scope global tailscale0"
    fi
    exit 0
fi
exit 0
EOF
    cat > "$wd/bin/am" << EOF
#!/bin/sh
printf '%s\n' "\$*" >> "$wd/am.log"
exit 0
EOF
    cat > "$wd/bin/dumpsys" << EOF
#!/bin/sh
cat "$wd/dumpsys.txt"
EOF
    chmod 755 "$wd/bin/ip" "$wd/bin/am" "$wd/bin/dumpsys"
    printf 'mCallState=0\n' > "$wd/dumpsys.txt"
    : > "$wd/am.log"
    cat > "$wd/state/config.env" << 'EOF'
CUP_HOST=127.0.0.1
CUP_PORT=1
CUP_USER=phonetunnel
KEEPALIVE_SEC=2
BACKOFF_INITIAL_SEC=1
BACKOFF_MAX_SEC=4
SSH_REMOTE_PORT=1
ADB_REMOTE_PORT=2
PHONE_SSH_PORT=3
PHONE_ADB_PORT=4
TS_PACKAGE=com.tailscale.ipn
TS_ACTIVITY=com.tailscale.ipn/com.tailscale.ipn.IPNActivity
TS_CHECK_SEC=1
TS_DOWN_STREAK=2
TS_RESTART_MIN_SEC=100
EOF
    local env_prefix
    env_prefix=(
        env
        PATH="$wd/bin:$PATH"
        IP_BIN="$wd/bin/ip"
        PHONETUNNEL_STATE="$wd/state"
        PHONETUNNEL_MODDIR="$MODULE"
        PHONETUNNEL_CONFIG="$wd/state/config.env"
        PHONETUNNEL_LOG="$wd/state/phonetunnel.log"
        PHONETUNNEL_WATCHDOG_SLEEP=0
    )
    {
        echo "drive watchdog $(date -Is)"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        echo "after one down check, am lines: $(wc -l < "$wd/am.log" | tr -d ' ')"
        [ "$(wc -l < "$wd/am.log" | tr -d ' ')" -eq 0 ]
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        echo "after streak 2, am log:"
        cat "$wd/am.log"
        [ "$(wc -l < "$wd/am.log" | tr -d ' ')" -eq 2 ]
        grep -q 'force-stop com.tailscale.ipn' "$wd/am.log"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        local lines
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "after rate-limited check, am lines: $lines"
        [ "$lines" -eq 2 ]

        printf '0\n' > "$wd/state/watchdog.last"
        printf 'mCallState=2\n' > "$wd/dumpsys.txt"
        printf '9\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "during call, am lines: $lines"
        [ "$lines" -eq 2 ]

        # A config that names the bridge package must not call am.
        sed 's/TS_PACKAGE=com.tailscale.ipn/TS_PACKAGE=com.gsm2computer.bridge/' \
            "$wd/state/config.env" > "$wd/state/config.bad"
        env PATH="$wd/bin:$PATH" \
            IP_BIN="$wd/bin/ip" \
            PHONETUNNEL_STATE="$wd/state" \
            PHONETUNNEL_MODDIR="$MODULE" \
            PHONETUNNEL_CONFIG="$wd/state/config.bad" \
            PHONETUNNEL_LOG="$wd/state/phonetunnel.log" \
            PHONETUNNEL_WATCHDOG_SLEEP=0 \
            sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "bridge package in config, am lines: $lines"
        [ "$lines" -eq 2 ]
        grep -q 'TS_PACKAGE must be com.tailscale.ipn' "$wd/state/phonetunnel.log"
        echo "watchdog ok"
    } | tee "$EVIDENCE/watchdog.txt"
}

assert_system_sshd_untouched() {
    local run now
    run=$(run_dir)
    now=$(sha256sum /etc/ssh/sshd_config)
    [ "$now" = "$(cat "$run/system-sshd.sha")" ] || die "/etc/ssh/sshd_config changed"
}

cmd_drive_all() {
    cmd_drive_tunnel
    cmd_drive_shell
    cmd_drive_adb
    cmd_drive_reconnect
    cmd_drive_watchdog
    assert_system_sshd_untouched
    local run
    run=$(run_dir)
    cp "$run/state/phonetunnel.log" "$EVIDENCE/phonetunnel.log"
    echo "system sshd_config unchanged" | tee "$EVIDENCE/system-sshd.txt"
    echo "drive-all ok" | tee "$EVIDENCE/drive-all.txt"
}

cmd_cleanup() {
    if [ ! -f "$RUNFILE" ]; then
        echo "nothing to clean"
        return 0
    fi
    local run
    run=$(cat "$RUNFILE")
    if [ -f "$run/iptables-dport" ]; then
        local port
        port=$(cat "$run/iptables-dport")
        sudo iptables -D OUTPUT -p tcp -d 127.0.0.1 --dport "$port" -j DROP 2>/dev/null || true
    fi
    if [ -f "$run/pids" ]; then
        local pid
        while read -r pid; do
            [ -n "$pid" ] || continue
            kill "$pid" 2>/dev/null || true
        done < "$run/pids"
    fi
    if [ -f "$run/state/supervisor.pid" ]; then
        kill "$(cat "$run/state/supervisor.pid")" 2>/dev/null || true
    fi
    if [ -f "$run/state/tunnel.pid" ]; then
        kill "$(cat "$run/state/tunnel.pid")" 2>/dev/null || true
    fi
    if [ -f "$run/state/dropbear.pid" ]; then
        kill "$(cat "$run/state/dropbear.pid")" 2>/dev/null || true
    fi
    if [ -f "$run/state/watchdog.pid" ]; then
        kill "$(cat "$run/state/watchdog.pid")" 2>/dev/null || true
    fi
    if [ -f "$run/sshd.pid" ]; then
        sudo kill "$(cat "$run/sshd.pid")" 2>/dev/null || true
    fi
    if [ -f "$run/authorized_keys.before" ]; then
        sudo cp -a "$run/authorized_keys.before" /home/phonetunnel/.ssh/authorized_keys
    elif [ -f "$run/real-home-keys" ]; then
        sudo rm -f /home/phonetunnel/.ssh/authorized_keys
    fi
    sleep 0.5
    # qemu children can outlive the wrapper pid. Kill any leftover by the run path.
    if [ -d "$run" ]; then
        local leftover
        leftover=$(ps -eo pid,args | awk -v r="$run" 'index($0, r) {print $1}')
        if [ -n "$leftover" ]; then
            # shellcheck disable=SC2086
            kill $leftover 2>/dev/null || true
        fi
    fi
    sudo rm -rf "$run"
    rm -f "$RUNFILE"
    echo "cleaned stand-in processes and $run"
    echo "evidence left in $EVIDENCE"
}

case "${1:-}" in
    doctor) cmd_doctor ;;
    launch) cmd_launch ;;
    drive-tunnel) cmd_drive_tunnel ;;
    drive-shell) cmd_drive_shell ;;
    drive-adb) cmd_drive_adb ;;
    drive-reconnect) cmd_drive_reconnect ;;
    drive-watchdog) cmd_drive_watchdog ;;
    drive-all) cmd_drive_all ;;
    cleanup) cmd_cleanup ;;
    *)
        echo "usage: harness.sh doctor|launch|drive-tunnel|drive-shell|drive-adb|drive-reconnect|drive-watchdog|drive-all|cleanup" >&2
        exit 2
        ;;
esac
