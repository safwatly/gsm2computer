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

phone_status() {
    local run="$1"
    if [ -z "$run" ]; then
        run=$(run_dir)
    fi
    PHONETUNNEL_STATE="$run/state" \
        PHONETUNNEL_MODDIR="$MODULE" \
        PHONETUNNEL_CONFIG="$run/state/config.env" \
        PHONETUNNEL_DBCLIENT="$run/bin/dbclient" \
        PHONETUNNEL_DROPBEAR="$run/bin/dropbear" \
        PHONETUNNEL_DROPBEARKEY="$run/bin/dropbearkey" \
        sh "$MODULE/bin/status.sh"
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
        command -v mksh >/dev/null || die "mksh missing (phone shell; large dumpsys test)"
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
                phone_status || true
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
    # nologin ignores ForceCommand. /bin/sh is what actually runs the hold script.
    sudo usermod -s /bin/sh phonetunnel

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
        phone_status "$run"
        phone_status "$run" > "$run/status.out"
        grep -q '^tunnel=up$' "$run/status.out"
        grep -q '^tunnel_session=up$' "$run/status.out"
        grep -q '^phone_sshd=listening$' "$run/status.out"
        grep -q '^phone_adb_managed=untouched$' "$run/status.out"
        grep -E -q '^host_fingerprint=SHA256:' "$run/status.out"

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
    # shellcheck disable=SC1091
    . "$run/ports"
    nonce="shell-$(date +%s)-$$"
    {
        echo "drive phone-shell $(date -Is)"
        shell_echo "$nonce"
        if grep -q 'no-pty' "$run/state/authorized_keys"; then
            die "shell key still sets no-pty, so a rescue shell cannot allocate a TTY"
        fi
        echo "shell key does not set no-pty"
        # qemu-user cannot set a controlling tty (TIOCSCTTY, missing pts slave).
        # A real PTY is an on-device check. Here we only require that the
        # failure, if any, is that qemu limit and not a rejected pty request.
        set +e
        ssh -tt -i "$run/phone_shell_ed25519" \
            -p "$SSH_REMOTE" \
            -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=yes \
            -o UserKnownHostsFile="$run/phone_known_hosts" \
            -o BatchMode=yes \
            -o ConnectTimeout=10 \
            "$(id -un)@127.0.0.1" 'if [ -t 0 ]; then echo pty=yes; else echo pty=no; fi' \
            > "$run/pty.out" 2> "$run/pty.err"
        set -e
        echo "pty stdout=$(tr -d '\r' < "$run/pty.out")"
        if grep -q 'pty=yes' "$run/pty.out"; then
            echo "pty allocated in this stand-in"
        else
            grep -E -q 'TIOCSCTTY|/dev/pts' "$run/state/phonetunnel.log" \
                || die "pty failed for a reason other than qemu-user tty setup"
            echo "pty not proven here: qemu-user cannot set a controlling tty"
        fi
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
mode=\$(cat "$wd/ip.mode" 2>/dev/null || echo missing)
dev=
for a in "\$@"; do
    case "\$a" in
        addr|show|-*) ;;
        *) dev=\$a ;;
    esac
done
if [ "\$mode" = error ]; then
    echo "ip: invalid argument" >&2
    exit 1
fi
if [ -n "\$dev" ] && [ "\$dev" != "\$mode" ] && [ "\$mode" != missing ] && [ "\$mode" != carrier ]; then
    echo "Device \\"\$dev\\" does not exist." >&2
    exit 1
fi
if [ "\$mode" = missing ]; then
    if [ -n "\$dev" ]; then
        echo "Device \\"\$dev\\" does not exist." >&2
        exit 1
    fi
    echo "1: lo: <LOOPBACK,UP> mtu 65536"
    echo "    inet 127.0.0.1/8 scope host lo"
    echo "2: wlan0: <BROADCAST,UP> mtu 1500"
    echo "    inet 192.168.1.20/24 scope global wlan0"
    exit 0
fi
if [ "\$mode" = carrier ]; then
    echo "8: rmnet_data0: <UP> mtu 1500"
    echo "    inet 100.80.1.2/24 scope global rmnet_data0"
    exit 0
fi
if [ "\$mode" = tun1 ]; then
    echo "8: rmnet_data0: <UP> mtu 1500"
    echo "    inet 100.80.1.2/24 scope global rmnet_data0"
    echo "11: tun1: <POINTOPOINT,UP> mtu 1280"
    echo "    inet 100.111.22.5/32 scope global tun1"
    exit 0
fi
if [ "\$mode" = up ]; then
    echo "3: tailscale0: <BROADCAST,UP> mtu 1280 qdisc fq_codel state UNKNOWN"
    echo "    inet 100.64.0.2/32 scope global tailscale0"
    exit 0
fi
if [ "\$mode" = noinet ]; then
    echo "3: tailscale0: <BROADCAST,UP> mtu 1280"
    echo "    inet6 fe80::1/64 scope link"
    exit 0
fi
echo "ip: unknown mode" >&2
exit 1
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
    printf 'missing\n' > "$wd/ip.mode"
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

        # Unknown telephony is not idle. A high streak and a cleared
        # rate-limit must still not call am.
        inhibit() {
            printf '0\n' > "$wd/state/watchdog.last"
            printf '9\n' > "$wd/state/watchdog.streak"
            "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
            lines=$(wc -l < "$wd/am.log" | tr -d ' ')
            echo "$1, am lines: $lines"
            [ "$lines" -eq 2 ]
        }
        : > "$wd/dumpsys.txt"
        inhibit "empty dumpsys"
        printf '%s\n' "Can't find service: telephony.registry" > "$wd/dumpsys.txt"
        inhibit "dumpsys error text"
        printf '%s\n' "mCallState=0" "mPreciseCallState=FG_ACTIVE" > "$wd/dumpsys.txt"
        inhibit "mPreciseCallState=FG_ACTIVE"
        printf '%s\n' \
            "mCallState=0" \
            "  mPreciseCallState=Ringing call state: 5, Foreground call state: -1, Background call state: -1, Disconnect cause: -1, Precise disconnect cause: -1" \
            > "$wd/dumpsys.txt"
        inhibit "precise ringing value 5"
        printf '%s\n' "Phone Id=0" "  mCallState=0" "Phone Id=1" "  mCallState=2" > "$wd/dumpsys.txt"
        inhibit "second subscription mCallState=2"
        printf 'mCallState=0\n' > "$wd/dumpsys.txt"
        printf 'error\n' > "$wd/ip.mode"
        inhibit "ip error"
        printf 'missing\n' > "$wd/ip.mode"
        mv "$wd/bin/dumpsys" "$wd/bin/dumpsys.hide"
        inhibit "dumpsys not on PATH"
        mv "$wd/bin/dumpsys.hide" "$wd/bin/dumpsys"

        # A real missing interface plus an explicit idle is still a restart.
        # mRingingCallState=0 must not be read as RINGING.
        printf '%s\n' "mCallState=0" "mRingingCallState=0" "mPreciseCallState=IDLE" > "$wd/dumpsys.txt"
        printf '0\n' > "$wd/state/watchdog.last"
        printf '1\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "after explicit idle and missing interface, am lines: $lines"
        [ "$lines" -eq 4 ]

        # tun1 with a CGNAT address is Tailscale, even next to a carrier
        # 100.64 address on rmnet. A high streak must not restart.
        printf 'tun1\n' > "$wd/ip.mode"
        printf '0\n' > "$wd/state/watchdog.last"
        printf '9\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "tun1 up beside carrier CGNAT, am lines: $lines"
        [ "$lines" -eq 4 ]
        PHONETUNNEL_STATE="$wd/state" \
            PHONETUNNEL_MODDIR="$MODULE" \
            PHONETUNNEL_CONFIG="$wd/state/config.env" \
            PHONETUNNEL_LOG="$wd/state/phonetunnel.log" \
            IP_BIN="$wd/bin/ip" \
            sh "$MODULE/bin/status.sh" > "$wd/status.tun1"
        grep -q '^tailscale=up$' "$wd/status.tun1"
        grep -q '^tailscale_iface=tun1$' "$wd/status.tun1"
        grep -q '^tailscale_addr=100.111.22.5$' "$wd/status.tun1"
        echo "status sees tun1"

        # Carrier CGNAT alone is not Tailscale. The miss still counts.
        printf 'carrier\n' > "$wd/ip.mode"
        printf '0\n' > "$wd/state/watchdog.last"
        printf '9\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "carrier CGNAT only, am lines: $lines"
        [ "$lines" -eq 6 ]

        # An explicit interface name overrides the scan.
        printf 'TS_IFACE=tun1\n' >> "$wd/state/config.env"
        printf 'tun1\n' > "$wd/ip.mode"
        printf '0\n' > "$wd/state/watchdog.last"
        printf '9\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "TS_IFACE=tun1, am lines: $lines"
        [ "$lines" -eq 6 ]
        printf 'TS_IFACE=notun\n' >> "$wd/state/config.env"
        printf '0\n' > "$wd/state/watchdog.last"
        printf '9\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" sh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "TS_IFACE=notun missing, am lines: $lines"
        [ "$lines" -eq 8 ]
        grep -v '^TS_IFACE=' "$wd/state/config.env" > "$wd/state/config.nots"
        mv "$wd/state/config.nots" "$wd/state/config.env"

        # A telephony dump bigger than one argument must still be readable.
        # mksh (the phone shell) execs printf, and a 200KB argument fails
        # with "Argument list too long". dash's builtin would hide that.
        python3 - << PY
from pathlib import Path
p = Path("$wd/dumpsys.txt")
def phone(pid, call="0", ring="0", fg="0", bg="0", pr="-1", pf="-1", pb="-1"):
    return (
        "Phone Id=%s\n"
        "  mCallState=%s\n"
        "  mCallIncomingNumber=\n"
        "  mRingingCallState=%s\n"
        "  mForegroundCallState=%s\n"
        "  mBackgroundCallState=%s\n"
        "  mPreciseCallState=Ringing call state: %s, Foreground call state: %s, "
        "Background call state: %s, Disconnect cause: -1, Precise disconnect cause: -1\n"
        "  mDataConnectionState=0\n\n"
    ) % (pid, call, ring, fg, bg, pr, pf, pb)
hist = [
    "11-09 01:00:00.100 notifyDataConnectionForSubscriber: phoneId=0 subId=1  state: CONNECTING, transport: WWAN, fail cause: NONE",
    "11-09 01:00:01.100 notifyDataConnectionForSubscriber: phoneId=0 subId=1  state: CONNECTED, transport: WWAN, fail cause: NONE",
    "11-09 01:00:02.100 notifyDataConnectionForSubscriber: phoneId=0 subId=1  state: DISCONNECTING, transport: WWAN, fail cause: LOST_CONNECTION",
    "11-09 01:00:03.100 notifyDataConnectionForSubscriber: phoneId=0 subId=1  state: DISCONNECTED, transport: WWAN, fail cause: LOST_CONNECTION",
    "11-09 01:02:00.000 notifyCallState: phoneId=0 subId=1 state=OFFHOOK incomingNumber=",
    "11-09 01:02:10.000 notifyCallState: phoneId=0 subId=1 state=RINGING incomingNumber=",
    "11-09 01:02:20.000 notifyCallState: phoneId=1 subId=2 CallState: RINGING",
    "11-09 01:02:30.000 notifyCallState: phoneId=1 subId=2 state: OFFHOOK",
    "11-09 01:03:00.000 notifyPreciseCallState: Ringing call state: 5, Foreground call state: 1, Background call state: -1, Disconnect cause: 16, Precise disconnect cause: 0",
    "11-09 01:03:01.000 mCellInfo=[CellInfoLte:{mRegistered=YES mCi=12345}]",
]
body = ["TelephonyRegistry:", "last known state:", "mNumPhones=2", ""]
body.append(phone(0))
body.append(phone(1))
body.append("local logs:")
chunk = ("\n".join(hist) + "\n").encode()
current = ("\n".join(body) + "\n").encode()
reps = (250 * 1024) // len(chunk) + 1
data = current + chunk * reps + current
p.write_bytes(data)
print(p.stat().st_size)
PY
        [ "$(wc -c < "$wd/dumpsys.txt" | tr -d ' ')" -gt 250000 ]
        printf 'missing\n' > "$wd/ip.mode"
        printf '0\n' > "$wd/state/watchdog.last"
        printf '1\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" mksh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "large idle dumpsys with call history, am lines: $lines"
        [ "$lines" -eq 10 ]
        if grep -q 'Argument list too long' "$wd/state/phonetunnel.log"; then
            echo "large dumpsys was passed as one argument" >&2
            exit 1
        fi
        python3 - << PY
from pathlib import Path
p = Path("$wd/dumpsys.txt")
text = p.read_text()
old = "Foreground call state: -1"
new = "Foreground call state: 1"
if old not in text:
    raise SystemExit("fixture has no current precise foreground field")
p.write_text(text.replace(old, new, 1))
PY
        printf '0\n' > "$wd/state/watchdog.last"
        printf '9\n' > "$wd/state/watchdog.streak"
        "${env_prefix[@]}" mksh "$MODULE/bin/watchdog.sh" once
        lines=$(wc -l < "$wd/am.log" | tr -d ' ')
        echo "large dump with current precise foreground 1, am lines: $lines"
        [ "$lines" -eq 10 ]

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
        [ "$lines" -eq 10 ]
        grep -q 'TS_PACKAGE must be com.tailscale.ipn' "$wd/state/phonetunnel.log"
        echo "watchdog ok"
    } | tee "$EVIDENCE/watchdog.txt"
}

cmd_drive_supervise() {
    local run
    run=$(run_dir)
    # shellcheck disable=SC1091
    . "$run/ports"
    local out="$EVIDENCE/supervise.txt"
    {
        echo "drive supervise $(date -Is)"
        local tun drop i nonce back
        tun=$(cat "$run/state/tunnel.pid")
        drop=$(cat "$run/state/dropbear.pid")
        echo "kill dropbear pid $drop; tunnel pid $tun must stay"
        kill "$drop"
        back=0
        for i in $(seq 1 20); do
            if [ -f "$run/state/dropbear.pid" ] \
                && [ "$(cat "$run/state/dropbear.pid")" != "$drop" ] \
                && tcp_open "$PHONE_SSH"; then
                back=1
                break
            fi
            sleep 1
        done
        echo "sshd restarted=$back tunnel_now=$(cat "$run/state/tunnel.pid" 2>/dev/null || echo none)"
        [ "$back" = 1 ]
        [ "$(cat "$run/state/tunnel.pid")" = "$tun" ]
        nonce="resshd-$(date +%s)"
        wait_shell "$nonce"
        echo "shell after sshd restart under the same tunnel ($nonce)"

        # A dropbear that exits immediately must not leave tunnel=up.
        mv "$run/bin/dropbear" "$run/bin/dropbear.real"
        printf '%s\n' '#!/bin/sh' 'exit 1' > "$run/bin/dropbear"
        chmod 755 "$run/bin/dropbear"
        kill "$(cat "$run/state/dropbear.pid")" 2>/dev/null || true
        back=0
        for i in $(seq 1 15); do
            phone_status "$run" > "$run/status.down" || true
            if grep -q '^phone_sshd=down$' "$run/status.down" \
                && grep -q '^tunnel=down$' "$run/status.down" \
                && grep -q '^tunnel_session=up$' "$run/status.down"; then
                back=1
                break
            fi
            sleep 1
        done
        echo "while sshd cannot start:"
        cat "$run/status.down" || true
        [ "$back" = 1 ]
        [ "$(cat "$run/state/tunnel.pid")" = "$tun" ]
        mv "$run/bin/dropbear.real" "$run/bin/dropbear"
        back=0
        for i in $(seq 1 20); do
            if tcp_open "$PHONE_SSH"; then
                phone_status "$run" > "$run/status.back" || true
                if grep -q '^tunnel=up$' "$run/status.back"; then
                    back=1
                    break
                fi
            fi
            sleep 1
        done
        echo "after dropbear restored, tunnel_up=$back"
        [ "$back" = 1 ]
        [ "$(cat "$run/state/tunnel.pid")" = "$tun" ]
        nonce="rebin-$(date +%s)"
        wait_shell "$nonce"
        echo "shell after binary restored ($nonce)"
        echo "supervise ok"
    } | tee "$out"
}

cmd_drive_policy() {
    local out="$EVIDENCE/policy.txt"
    {
        echo "drive policy $(date -Is)"
        local plog
        plog=$(mktemp -d)
        trap 'if [ -f /run/phonetunnel-policy/ok/sshd.pid ]; then sudo kill "$(cat /run/phonetunnel-policy/ok/sshd.pid)" 2>/dev/null || true; fi; if [ -n "${dpid:-}" ]; then kill "$dpid" 2>/dev/null || true; fi; sudo rm -rf /run/phonetunnel-policy; sudo rm -f /tmp/pt-stream-sock; rm -rf "'"$plog"'"' EXIT
        local hits
        hits=$(grep -R -n -E 'setprop|persist\.adb|service\.adb|start adbd' "$MODULE" --include='*.sh' \
            | grep -v -E ':[0-9]+:[[:space:]]*#' || true)
        if [ -n "$hits" ]; then
            printf '%s\n' "$hits"
            die "module scripts still reconfigure adbd"
        fi
        echo "module scripts do not set adb tcp properties or start adbd"

        local tmp
        tmp=$(mktemp -d)
        cat > "$tmp/config.env" << 'EOF'
CUP_HOST=127.0.0.1
CUP_PORT=1
CUP_USER=phonetunnel
KEEPALIVE_SEC=0
BACKOFF_INITIAL_SEC=0
BACKOFF_MAX_SEC=0
SSH_REMOTE_PORT=2
ADB_REMOTE_PORT=3
PHONE_SSH_PORT=4
PHONE_ADB_PORT=5
TS_PACKAGE=com.tailscale.ipn
TS_ACTIVITY=com.tailscale.ipn/com.tailscale.ipn.IPNActivity
TS_CHECK_SEC=0
TS_DOWN_STREAK=0
TS_RESTART_MIN_SEC=0
EOF
        PHONETUNNEL_STATE="$tmp" \
            PHONETUNNEL_LOG="$tmp/phonetunnel.log" \
            PHONETUNNEL_CONFIG="$tmp/config.env" \
            PHONETUNNEL_MODDIR="$MODULE" \
            sh -c '
                . "$PHONETUNNEL_MODDIR/bin/common.sh"
                load_config
                echo "check=$TS_CHECK_SEC"
                echo "restart=$TS_RESTART_MIN_SEC"
                echo "streak=$TS_DOWN_STREAK"
                echo "keep=$KEEPALIVE_SEC"
                echo "backoff=$BACKOFF_INITIAL_SEC"
                echo "clamped=$PHONETUNNEL_TIMERS_CLAMPED"
            ' > "$tmp/timers.out"
        cat "$tmp/timers.out"
        grep -q '^check=15$' "$tmp/timers.out"
        grep -q '^restart=60$' "$tmp/timers.out"
        grep -q '^streak=2$' "$tmp/timers.out"
        grep -q '^keep=45$' "$tmp/timers.out"
        grep -q '^clamped=1$' "$tmp/timers.out"
        rm -rf "$tmp"
        echo "zero timers were raised"

        sudo mkdir -p /run/sshd /run/phonetunnel-policy
        sudo chmod 755 /run/phonetunnel-policy
        # shell is /bin/sh so ForceCommand is actually executed.
        sudo usermod -s /bin/sh phonetunnel

        policy_fail_closed() {
            local work before after
            work=/run/phonetunnel-policy/conflict
            sudo rm -rf "$work"
            sudo mkdir -p "$work/etc/ssh" "$work/inc"
            sudo ssh-keygen -t ed25519 -f "$work/etc/ssh/ssh_host_ed25519_key" -N "" -C policy >/dev/null
            sudo chmod 600 "$work/etc/ssh/ssh_host_ed25519_key"
            sudo tee "$work/inc/loose.conf" >/dev/null << 'EOF'
Match User phonetunnel
    AllowTcpForwarding yes
    PermitOpen any
    AllowStreamLocalForwarding yes
EOF
            sudo tee "$work/etc/ssh/sshd_config" >/dev/null << EOF
Include $work/inc/loose.conf
Port 1
ListenAddress 127.0.0.1
HostKey $work/etc/ssh/ssh_host_ed25519_key
UsePAM no
PasswordAuthentication no
PubkeyAuthentication yes
EOF
            before=$(sudo sha256sum "$work/etc/ssh/sshd_config")
            if sudo "$SETUP" --root "$work" --cup-host 127.0.0.1 --cup-port 1 \
                --ssh-port 2222 --adb-port 5556 >"$plog/conflict.out" 2>"$plog/conflict.err"; then
                echo "conflict setup unexpectedly succeeded"
                cat "$plog/conflict.err"
                die "loose Include Match was accepted"
            fi
            after=$(sudo sha256sum "$work/etc/ssh/sshd_config")
            echo "conflict setup refused"
            cat "$plog/conflict.err"
            [ "$before" = "$after" ]
            if sudo grep -q 'BEGIN gsm2computer-phonetunnel' "$work/etc/ssh/sshd_config"; then
                die "failed setup left its Match block in place"
            fi
            echo "conflict config restored byte for byte"
        }
        policy_fail_closed

        local work port ssh_r adb_r
        work=/run/phonetunnel-policy/ok
        port=$(free_port)
        ssh_r=$(free_port)
        adb_r=$(free_port)
        sudo rm -rf "$work"
        sudo mkdir -p "$work/etc/ssh"
        sudo ssh-keygen -t ed25519 -f "$work/etc/ssh/ssh_host_ed25519_key" -N "" -C policyok >/dev/null
        sudo chmod 600 "$work/etc/ssh/ssh_host_ed25519_key"
        # A later loose Match must lose, because the script inserts first.
        sudo tee "$work/etc/ssh/sshd_config" >/dev/null << EOF
Port $port
ListenAddress 127.0.0.1
HostKey $work/etc/ssh/ssh_host_ed25519_key
PidFile $work/sshd.pid
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile $work/home/phonetunnel/.ssh/authorized_keys
Match User phonetunnel
    AllowTcpForwarding yes
    PermitOpen any
    AllowStreamLocalForwarding yes
EOF
        sudo "$SETUP" --root "$work" --cup-host 127.0.0.1 --cup-port "$port" \
            --ssh-port "$ssh_r" --adb-port "$adb_r" >"$plog/ok-setup.out"
        echo "strict Match inserted ahead of a later loose Match"
        sudo /usr/sbin/sshd -T -C user=phonetunnel,host=127.0.0.1,addr=127.0.0.1 \
            -f "$work/etc/ssh/sshd_config" | awk '$1 ~ /^(allowtcpforwarding|allowstreamlocalforwarding|permitopen|permittty|forcecommand)$/'
        sudo chmod 755 "$work" "$work/home" "$work/home/phonetunnel" "$work/home/phonetunnel/.ssh" || true
        sudo chown -R phonetunnel:phonetunnel "$work/home/phonetunnel"
        sudo chmod 700 "$work/home/phonetunnel" "$work/home/phonetunnel/.ssh"
        sudo chmod 600 "$work/home/phonetunnel/.ssh/authorized_keys"
        sudo mkdir -p /run/sshd
        sudo /usr/sbin/sshd -f "$work/etc/ssh/sshd_config"
        sleep 0.3

        local id_ossh
        id_ossh=$plog/id_openssh
        sudo cp "$work/root/phonetunnel-install/id_ed25519" "$plog/id_dropbear"
        sudo chown "$(id -u):$(id -g)" "$plog/id_dropbear"
        chmod 600 "$plog/id_dropbear"
        dropbearconvert dropbear openssh "$plog/id_dropbear" "$id_ossh"
        chmod 600 "$id_ossh"

        local cout cerr
        cout=$plog/cmd.out
        cerr=$plog/cmd.err
        if ssh -i "$id_ossh" -o IdentitiesOnly=yes -o BatchMode=yes \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -p "$port" phonetunnel@127.0.0.1 'echo pwned' >"$cout" 2>"$cerr"; then
            die "phonetunnel command session succeeded"
        fi
        echo "command stdout=$(cat "$cout")"
        echo "command stderr=$(cat "$cerr")"
        grep -q 'phonetunnel: command refused' "$cerr"
        if grep -q pwned "$cout"; then
            die "requested command was executed"
        fi
        if grep -q 'not available' "$cerr"; then
            die "nologin swallowed ForceCommand"
        fi

        ssh -i "$id_ossh" -o IdentitiesOnly=yes -o BatchMode=yes \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -p "$port" -N -T -o ExitOnForwardFailure=yes \
            -R "127.0.0.1:${ssh_r}:127.0.0.1:9" phonetunnel@127.0.0.1 \
            >"$plog/r.out" 2>"$plog/r.err" &
        local rpid=$!
        sleep 0.6
        if ! kill -0 "$rpid" 2>/dev/null; then
            cat "$plog/r.err"
            die "allowed reverse forward failed"
        fi
        kill "$rpid" 2>/dev/null || true
        wait "$rpid" 2>/dev/null || true
        echo "allowed -R stayed up"

        if ssh -i "$id_ossh" -o IdentitiesOnly=yes -o BatchMode=yes \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -p "$port" -N -T -o ExitOnForwardFailure=yes \
            -R "127.0.0.1:39999:127.0.0.1:9" phonetunnel@127.0.0.1 \
            >"$plog/bad.out" 2>"$plog/bad.err"; then
            die "disallowed -R was accepted"
        fi
        echo "disallowed -R refused"

        local dest lport
        dest=$(free_port)
        python3 - "$dest" << 'PY' &
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen(5)
while True:
    c, _ = s.accept()
    c.sendall(b"dest-ok\n")
    c.close()
PY
        local dpid=$!
        sleep 0.2
        lport=$(free_port)
        ssh -i "$id_ossh" -o IdentitiesOnly=yes -o BatchMode=yes \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -p "$port" -N -T -o ExitOnForwardFailure=yes \
            -L "127.0.0.1:${lport}:127.0.0.1:${dest}" phonetunnel@127.0.0.1 \
            >"$plog/l.out" 2>"$plog/l.err" &
        local lpid=$!
        sleep 0.5
        python3 - "$lport" << 'PY' > "$plog/l.probe" 2>&1 || true
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    s.sendall(b"x")
    data = s.recv(32)
    sys.stdout.write(repr(data))
except Exception as e:
    sys.stdout.write(type(e).__name__ + ": " + str(e))
PY
        kill "$lpid" 2>/dev/null || true
        wait "$lpid" 2>/dev/null || true
        kill "$dpid" 2>/dev/null || true
        echo "local forward probe: $(cat "$plog/l.probe")"
        echo "local forward log: $(cat "$plog/l.err")"
        if grep -q 'dest-ok' "$plog/l.probe"; then
            die "-L reached a destination"
        fi
        grep -E -q 'administratively prohibited|ConnectionReset|Connection reset|refused' \
            "$plog/l.err" "$plog/l.probe"

        sudo rm -f /tmp/pt-stream-sock
        if ssh -i "$id_ossh" -o IdentitiesOnly=yes -o BatchMode=yes \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -p "$port" -N -T -o ExitOnForwardFailure=yes \
            -R "/tmp/pt-stream-sock:127.0.0.1:9" phonetunnel@127.0.0.1 \
            >"$plog/s.out" 2>"$plog/s.err"; then
            die "unix-socket forward was accepted"
        fi
        if [ -S /tmp/pt-stream-sock ]; then
            die "unix-socket forward created a socket"
        fi
        echo "streamlocal refused: $(grep -E 'Error|administratively' "$plog/s.err" || true)"
        sudo kill "$(cat "$work/sshd.pid")" 2>/dev/null || true
        echo "policy ok"
    } | tee "$out"
    sudo rm -rf /run/phonetunnel-policy
    sudo rm -f /tmp/pt-stream-sock
}

cmd_drive_account() {
    local out="$EVIDENCE/account.txt"
    local host_before hold_before
    host_before=$(sudo sha256sum /etc/passwd /etc/shadow /etc/group /etc/gshadow /etc/ssh/sshd_config)
    if [ -f /usr/local/sbin/phonetunnel-hold ]; then
        hold_before=$(sha256sum /usr/local/sbin/phonetunnel-hold)
    else
        hold_before=absent
    fi
    {
        echo "drive account $(date -Is)"
        # Pid reuse: a pidfile whose cmdline is not this module is not killed.
        local ustate unrelated owned
        ustate=$(mktemp -d)
        sleep 120 &
        unrelated=$!
        printf '%s\n' "$unrelated" > "$ustate/dropbear.pid"
        PHONETUNNEL_STATE="$ustate" sh "$MODULE/uninstall.sh"
        if ! kill -0 "$unrelated" 2>/dev/null; then
            die "uninstall killed a pid whose cmdline is not dropbear"
        fi
        kill "$unrelated" 2>/dev/null || true
        wait "$unrelated" 2>/dev/null || true
        bash -c 'exec -a dropbear sleep 120' &
        owned=$!
        printf '%s\n' "$owned" > "$ustate/dropbear.pid"
        PHONETUNNEL_STATE="$ustate" sh "$MODULE/uninstall.sh"
        if kill -0 "$owned" 2>/dev/null; then
            kill "$owned" 2>/dev/null || true
            die "uninstall left a dropbear-named pid running"
        fi
        wait "$owned" 2>/dev/null || true
        rm -rf "$ustate"
        echo "uninstall skipped a reused pid and stopped a dropbear cmdline"

        sudo unshare --mount --propagation private bash -s -- "$SETUP" << 'EOS'
set -euo pipefail
setup=$1
root=$(mktemp -d /tmp/pt-account.XXXX)
mkdir -p "$root"/{etc/ssh,etc/skel,etc/pam.d,home,root,tmp,proc,dev,bin,lib,usr/local/bin,usr/local/sbin,var,run/sshd}
chmod 755 "$root/run/sshd"
chmod 1777 "$root/tmp"
cp -a /etc/passwd /etc/group /etc/shadow /etc/gshadow /etc/login.defs /etc/shells "$root/etc/"
sed -i '/^phonetunnel:/d' "$root/etc/passwd" "$root/etc/shadow" "$root/etc/group" "$root/etc/gshadow" || true
cat > "$root/etc/nsswitch.conf" << 'EOF'
passwd: files
group: files
shadow: files
EOF
if [ -d /etc/skel ]; then cp -a /etc/skel/. "$root/etc/skel/" 2>/dev/null || true; fi
ssh-keygen -t ed25519 -f "$root/etc/ssh/ssh_host_ed25519_key" -N "" -q
cat > "$root/etc/ssh/sshd_config" << 'EOF'
Port 22
HostKey /etc/ssh/ssh_host_ed25519_key
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
cp -a "$root/etc/ssh/sshd_config" "$root/sshd.orig"
cat > "$root/usr/local/bin/systemctl" << 'EOF'
#!/bin/sh
if [ "${1:-}" = reload ] && { [ "${2:-}" = ssh ] || [ "${2:-}" = sshd ]; }; then
    exit 0
fi
exit 1
EOF
chmod 755 "$root/usr/local/bin/systemctl"
mount --bind /bin "$root/bin"
mount --bind /lib "$root/lib"
if [ -d /lib64 ]; then mkdir -p "$root/lib64"; mount --bind /lib64 "$root/lib64"; fi
mount --bind /usr "$root/usr"
mount -t tmpfs tmpfs "$root/usr/local"
mkdir -p "$root/usr/local/bin" "$root/usr/local/sbin"
# tmpfs hid the copy made before the /usr bind. Write it on the private mount.
cat > "$root/usr/local/bin/systemctl" << 'EOF'
#!/bin/sh
if [ "${1:-}" = reload ] && { [ "${2:-}" = ssh ] || [ "${2:-}" = sshd ]; }; then
    exit 0
fi
exit 1
EOF
chmod 755 "$root/usr/local/bin/systemctl"
mkdir -p "$root/agent/repos/gsm2computer"
mount --bind /agent/repos/gsm2computer "$root/agent/repos/gsm2computer"
mkdir -p "$root/etc/alternatives"
mount --bind /etc/alternatives "$root/etc/alternatives"
mount --bind /dev "$root/dev"
mount -t proc proc "$root/proc"

run_setup() {
    _fail="${1:-}"
    shift || true
    chroot "$root" /usr/bin/env \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        PHONETUNNEL_SETUP_FAIL_AFTER="$_fail" \
        /bin/bash "$setup" "$@"
}

clean_tree() {
    [ ! -e "$root/usr/local/sbin/phonetunnel-hold" ]
    ! grep -q '^phonetunnel:' "$root/etc/passwd"
    [ ! -d "$root/root/phonetunnel-install" ]
    cmp -s "$root/sshd.orig" "$root/etc/ssh/sshd_config"
}

echo "---- conflict before any account ----"
cat > "$root/etc/ssh/loose.conf" << 'EOF'
Match User phonetunnel
    AllowTcpForwarding yes
    PermitOpen any
    AllowStreamLocalForwarding yes
EOF
printf '\nInclude /etc/ssh/loose.conf\n' >> "$root/etc/ssh/sshd_config"
cp -a "$root/etc/ssh/sshd_config" "$root/sshd.orig"
set +e
run_setup >"$root/conflict.out" 2>"$root/conflict.err"
crc=$?
set -e
echo "conflict rc=$crc"
cat "$root/conflict.err"
[ "$crc" -ne 0 ]
grep -q 'not the tunnel policy' "$root/conflict.err"
clean_tree
echo "conflict left no user, hold script, or sshd edit"

echo "---- forced failure after hold ----"
cp -a "$root/sshd.base" "$root/etc/ssh/sshd_config" 2>/dev/null || true
# sshd.orig currently includes the loose Match. Restore the base.
python3 - << PY
from pathlib import Path
p = Path("$root/etc/ssh/sshd_config")
text = p.read_text()
text = text.split("\nInclude /etc/ssh/loose.conf")[0]
if not text.endswith("\n"):
    text += "\n"
p.write_text(text)
PY
cp -a "$root/etc/ssh/sshd_config" "$root/sshd.orig"
set +e
run_setup hold >"$root/fail.out" 2>"$root/fail.err"
frc=$?
set -e
echo "fail-after-hold rc=$frc"
cat "$root/fail.err"
[ "$frc" -ne 0 ]
grep -q 'forced failure after hold' "$root/fail.err"
grep -q 'rolling back' "$root/fail.err"
clean_tree
echo "failed run rolled back the new user and the hold script"

echo "---- fresh account ----"
set +e
run_setup >"$root/ok.out" 2>"$root/ok.err"
ok=$?
set -e
echo "fresh rc=$ok"
cat "$root/ok.err"
[ "$ok" -eq 0 ]
shell=$(awk -F: '$1=="phonetunnel" {print $7}' "$root/etc/passwd")
shadow=$(awk -F: '$1=="phonetunnel" {print $2}' "$root/etc/shadow")
echo "shell=$shell shadow=$shadow"
[ "$shell" = /bin/sh ]
[ "$shadow" = '*' ]
[ -x "$root/usr/local/sbin/phonetunnel-hold" ]
[ -s "$root/root/phonetunnel-install/id_ed25519" ]
begins=$(grep -c 'BEGIN gsm2computer-phonetunnel' "$root/etc/ssh/sshd_config")
[ "$begins" -eq 1 ]
chroot "$root" /usr/sbin/sshd -T -C user=phonetunnel,host=127.0.0.1,addr=127.0.0.1 \
    -f /etc/ssh/sshd_config | awk '$1=="allowtcpforwarding" || $1=="forcecommand"'
pub1=$(awk '{print $2}' "$root/root/phonetunnel-install/id_ed25519.pub")
if compgen -G "$root/etc/ssh/sshd_config.tmp.*" >/dev/null; then
    echo "sshd temp file left behind after a successful install" >&2
    exit 1
fi

echo "---- refuse a password account ----"
chroot "$root" useradd --create-home --shell /bin/bash --home-dir /home/nottunnel nottunnel
hash='$6$testsalt$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
chroot "$root" usermod -p "$hash" nottunnel
chmod 755 "$root/home/nottunnel"
shadow_before=$(awk -F: '$1=="nottunnel" {print $2}' "$root/etc/shadow")
[ "$shadow_before" = "$hash" ]
sshd_before=$(sha256sum "$root/etc/ssh/sshd_config")
set +e
run_setup '' --user nottunnel >"$root/refuse.out" 2>"$root/refuse.err"
rrc=$?
set -e
echo "refuse rc=$rrc"
cat "$root/refuse.err"
[ "$rrc" -ne 0 ]
grep -q 'refusing to adopt' "$root/refuse.err"
grep -q -- '--adopt-existing' "$root/refuse.err"
if grep -q 'rolling back' "$root/refuse.err"; then
    echo "refusal tried to roll the account back" >&2
    exit 1
fi
[ "$(awk -F: '$1=="nottunnel" {print $2}' "$root/etc/shadow")" = "$hash" ]
[ "$(awk -F: '$1=="nottunnel" {print $7}' "$root/etc/passwd")" = /bin/bash ]
[ "$(stat -c %a "$root/home/nottunnel")" = 755 ]
[ "$(sha256sum "$root/etc/ssh/sshd_config")" = "$sshd_before" ]
echo "refused a password account without changing it"

echo "---- refuse a locked account that is not this tunnel ----"
chroot "$root" useradd --system --create-home --shell /usr/sbin/nologin --home-dir /home/spare spare
spare_shadow=$(awk -F: '$1=="spare" {print $2}' "$root/etc/shadow")
spare_shell=$(awk -F: '$1=="spare" {print $7}' "$root/etc/passwd")
set +e
run_setup '' --user spare >"$root/spare.out" 2>"$root/spare.err"
src=$?
set -e
echo "spare rc=$src"
cat "$root/spare.err"
[ "$src" -ne 0 ]
grep -q 'refusing to adopt' "$root/spare.err"
[ "$(awk -F: '$1=="spare" {print $2}' "$root/etc/shadow")" = "$spare_shadow" ]
[ "$(awk -F: '$1=="spare" {print $7}' "$root/etc/passwd")" = "$spare_shell" ]
[ "$(sha256sum "$root/etc/ssh/sshd_config")" = "$sshd_before" ]
echo "refused a locked nologin account"

echo "---- refuse an empty password on a tunnel-shaped account ----"
chroot "$root" useradd --system --create-home --shell /bin/sh --home-dir /home/empty empty
python3 - << PY
from pathlib import Path
p = Path("$root/etc/shadow")
lines = []
for line in p.read_text().splitlines():
    parts = line.split(":")
    if parts and parts[0] == "empty":
        parts[1] = ""
    lines.append(":".join(parts))
p.write_text("\n".join(lines) + "\n")
PY
mkdir -p "$root/home/empty/.ssh"
printf '%s\n' 'ssh-ed25519 AAAA phonetunnel-phone' > "$root/home/empty/.ssh/authorized_keys"
[ -z "$(awk -F: '$1=="empty" {print $2}' "$root/etc/shadow")" ]
set +e
run_setup '' --user empty >"$root/empty.out" 2>"$root/empty.err"
erc=$?
set -e
echo "empty rc=$erc"
cat "$root/empty.err"
[ "$erc" -ne 0 ]
grep -q 'refusing to adopt' "$root/empty.err"
if grep -q 'rolling back' "$root/empty.err"; then
    echo "empty-password refusal tried to roll the account back" >&2
    exit 1
fi
[ -z "$(awk -F: '$1=="empty" {print $2}' "$root/etc/shadow")" ]
[ "$(awk -F: '$1=="empty" {print $7}' "$root/etc/passwd")" = /bin/sh ]
[ "$(sha256sum "$root/etc/ssh/sshd_config")" = "$sshd_before" ]
echo "refused an empty password on a tunnel-shaped account"

echo "---- failed re-run restores home mode ----"
chmod 711 "$root/home/phonetunnel"
set +e
run_setup user >"$root/mode.out" 2>"$root/mode.err"
mrc=$?
set -e
echo "mode-rollback rc=$mrc"
cat "$root/mode.err"
[ "$mrc" -ne 0 ]
grep -q 'forced failure after user' "$root/mode.err"
grep -q 'rolling back' "$root/mode.err"
[ "$(stat -c %a "$root/home/phonetunnel")" = 711 ]
[ "$(awk -F: '$1=="phonetunnel" {print $2}' "$root/etc/shadow")" = '*' ]
[ "$(awk -F: '$1=="phonetunnel" {print $7}' "$root/etc/passwd")" = /bin/sh ]
echo "failed re-run restored home mode, shadow, and shell"

echo "---- adopt then roll back the password ----"
set +e
run_setup user --adopt-existing --user nottunnel >"$root/adopt.out" 2>"$root/adopt.err"
adrc=$?
set -e
echo "adopt-rollback rc=$adrc"
cat "$root/adopt.err"
[ "$adrc" -ne 0 ]
grep -q 'forced failure after user' "$root/adopt.err"
grep -q 'rolling back' "$root/adopt.err"
[ "$(awk -F: '$1=="nottunnel" {print $2}' "$root/etc/shadow")" = "$hash" ]
[ "$(awk -F: '$1=="nottunnel" {print $7}' "$root/etc/passwd")" = /bin/bash ]
[ "$(stat -c %a "$root/home/nottunnel")" = 755 ]
[ "$(awk -F: '$1=="phonetunnel" {print $2}' "$root/etc/shadow")" = '*' ]
[ "$(sha256sum "$root/etc/ssh/sshd_config")" = "$sshd_before" ]
echo "adopt failure restored the password, shell, and home mode"

echo "---- second run ----"
printf 'kept-phone-hostkey\n' > "$root/root/phonetunnel-install/phone_known_hosts"
printf 'operator-note\n' > "$root/root/phonetunnel-install/operator-note.txt"
chmod 600 "$root/root/phonetunnel-install/phone_known_hosts" \
    "$root/root/phonetunnel-install/operator-note.txt"
set +e
run_setup >"$root/again.out" 2>"$root/again.err"
arc=$?
set -e
echo "second rc=$arc"
cat "$root/again.err"
[ "$arc" -eq 0 ]
pub2=$(awk '{print $2}' "$root/root/phonetunnel-install/id_ed25519.pub")
[ "$pub1" = "$pub2" ]
begins=$(grep -c 'BEGIN gsm2computer-phonetunnel' "$root/etc/ssh/sshd_config")
[ "$begins" -eq 1 ]
[ "$(cat "$root/root/phonetunnel-install/phone_known_hosts")" = "kept-phone-hostkey" ]
[ "$(cat "$root/root/phonetunnel-install/operator-note.txt")" = "operator-note" ]
[ "$(stat -c %a "$root/home/phonetunnel")" = 750 ]
if compgen -G "$root/etc/ssh/sshd_config.tmp.*" >/dev/null; then
    echo "sshd temp file left behind after the second run" >&2
    exit 1
fi
echo "second run reused the key, kept extra bundle files, and did not stack Match blocks"
echo "account ok"
EOS
        echo "host passwd and sshd were not the chroot"
    } | tee "$out"
    local host_after hold_after
    host_after=$(sudo sha256sum /etc/passwd /etc/shadow /etc/group /etc/gshadow /etc/ssh/sshd_config)
    if [ -f /usr/local/sbin/phonetunnel-hold ]; then
        hold_after=$(sha256sum /usr/local/sbin/phonetunnel-hold)
    else
        hold_after=absent
    fi
    [ "$host_before" = "$host_after" ] || die "host passwd or sshd_config changed"
    [ "$hold_before" = "$hold_after" ] || die "host hold script changed"
    grep -q 'account ok' "$out"
}

assert_system_sshd_untouched() {
    local run now
    run=$(run_dir)
    now=$(sha256sum /etc/ssh/sshd_config)
    [ "$now" = "$(cat "$run/system-sshd.sha")" ] || die "/etc/ssh/sshd_config changed"
}

cmd_drive_nopasswd() {
    local out="$EVIDENCE/nopasswd.txt"
    local work port sp before after
    command -v qemu-aarch64-static >/dev/null || die "qemu-aarch64-static missing"
    command -v mksh >/dev/null || true
    work=$(mktemp -d)
    port=$(free_port)
    sp=""
    before=$(sudo sha256sum /etc/passwd /etc/group /etc/shells)
    sudo rm -rf /run/phonetunnel-nopasswd
    cleanup_np() {
        if [ -n "${sp:-}" ]; then
            sudo kill "$sp" 2>/dev/null || true
            wait "$sp" 2>/dev/null || true
        fi
        if [ -f "$work/dropbear.pid" ]; then
            sudo kill "$(cat "$work/dropbear.pid")" 2>/dev/null || true
        fi
        sudo rm -rf /run/phonetunnel-nopasswd
        rm -rf "$work"
    }
    trap cleanup_np RETURN
    ssh-keygen -t ed25519 -f "$work/id" -N "" -q
    qemu-aarch64-static "$MULTI" dropbearkey -t ed25519 -f "$work/hostkey" >"$work/keygen.out"
    chmod 600 "$work/hostkey" "$work/id"
    printf '%s\n' 'nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin' > "$work/passwd"
    printf '%s\n' 'nobody:x:65534:' > "$work/group"
    : > "$work/shells"
    sudo mkdir -p /run/phonetunnel-nopasswd/keys
    sudo chmod 755 /run/phonetunnel-nopasswd /run/phonetunnel-nopasswd/keys
    sudo cp "$work/hostkey" /run/phonetunnel-nopasswd/hostkey
    sudo cp "$work/id.pub" /run/phonetunnel-nopasswd/keys/authorized_keys
    sudo chmod 600 /run/phonetunnel-nopasswd/hostkey /run/phonetunnel-nopasswd/keys/authorized_keys
    sudo chown root:root /run/phonetunnel-nopasswd /run/phonetunnel-nopasswd/keys \
        /run/phonetunnel-nopasswd/hostkey /run/phonetunnel-nopasswd/keys/authorized_keys
    {
        echo "drive nopasswd $(date -Is)"
        sudo unshare --mount --propagation private bash -c "
set -euo pipefail
mount --bind '$work/passwd' /etc/passwd
mount --bind '$work/group' /etc/group
if [ -e /etc/shells ]; then mount --bind '$work/shells' /etc/shells; fi
if grep -q '^root:' /etc/passwd; then
    echo 'root is still in the mounted passwd' >&2
    exit 1
fi
echo \$\$ > '$work/dropbear.pid'
exec qemu-aarch64-static '$MULTI' dropbear -F \
    -p '127.0.0.1:$port' \
    -r /run/phonetunnel-nopasswd/hostkey \
    -D /run/phonetunnel-nopasswd/keys \
    -j -k
" >"$work/dropbear.log" 2>&1 &
        sp=$!
        i=0
        for i in $(seq 1 30); do
            if tcp_open "$port"; then
                break
            fi
            sleep 0.2
        done
        if ! tcp_open "$port"; then
            echo "dropbear did not listen" >&2
            cat "$work/dropbear.log" >&2 || true
            exit 1
        fi
        nonce="nopasswd-$(date +%s)-$$"
        got=$(ssh -i "$work/id" -p "$port" \
            -o IdentitiesOnly=yes \
            -o BatchMode=yes \
            -o StrictHostKeyChecking=no \
            -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=10 \
            root@127.0.0.1 "id -u; printf '%s\n' '$nonce'")
        echo "root login:"
        printf '%s\n' "$got"
        printf '%s\n' "$got" | grep -q '^0$'
        printf '%s\n' "$got" | grep -q "$nonce"
        if grep -q 'nonexistent user' "$work/dropbear.log"; then
            echo "root login was logged as nonexistent" >&2
            exit 1
        fi
        set +e
        ssh -i "$work/id" -p "$port" \
            -o IdentitiesOnly=yes \
            -o BatchMode=yes \
            -o StrictHostKeyChecking=no \
            -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=5 \
            ghost@127.0.0.1 true >"$work/ghost.out" 2>"$work/ghost.err"
        grc=$?
        set -e
        echo "ghost rc=$grc"
        cat "$work/ghost.err"
        [ "$grc" -ne 0 ]
        grep 'nonexistent user' "$work/dropbear.log"
        echo "root logged in with no passwd entry; ghost was rejected"
        echo "nopasswd ok"
    } | tee "$out"
    after=$(sudo sha256sum /etc/passwd /etc/group /etc/shells)
    [ "$before" = "$after" ] || die "host passwd, group, or shells changed"
    grep -q 'nopasswd ok' "$out"
}

cmd_drive_all() {
    cmd_drive_tunnel
    cmd_drive_shell
    cmd_drive_adb
    cmd_drive_reconnect
    cmd_drive_supervise
    cmd_drive_watchdog
    cmd_drive_nopasswd
    cmd_drive_policy
    cmd_drive_account
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
    drive-nopasswd) cmd_drive_nopasswd ;;
    drive-supervise) cmd_drive_supervise ;;
    drive-policy) cmd_drive_policy ;;
    drive-account) cmd_drive_account ;;
    drive-all) cmd_drive_all ;;
    cleanup) cmd_cleanup ;;
    *)
        echo "usage: harness.sh doctor|launch|drive-tunnel|drive-shell|drive-adb|drive-reconnect|drive-supervise|drive-watchdog|drive-nopasswd|drive-policy|drive-account|drive-all|cleanup" >&2
        exit 2
        ;;
esac
