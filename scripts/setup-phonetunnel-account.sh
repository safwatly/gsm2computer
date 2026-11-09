#!/bin/bash
# Create the cup account that the phone's reverse tunnel logs into.
#
# The account can open only two remote forwards, both on 127.0.0.1:
#   SSH_REMOTE_PORT (default 2222) and ADB_REMOTE_PORT (default 5556).
# It cannot get a shell, a TTY, an agent, or a local (-L) forward.
# Keys are generated here. Nothing secret is written into the git repo.
#
# Usage (on cup, as root):
#   sudo ./scripts/setup-phonetunnel-account.sh
#
# --dry-run prints the plan and does not create files, users, or reload sshd.
# --root DIR writes under DIR instead of / and does not useradd or reload.
#   The stand-in harness uses that. Do not point --root at / unless you
#   mean the real cup sshd.
set -euo pipefail

DRY=0
ROOT_DIR="/"
CUP_HOST="62.83.35.197"
CUP_PORT="22"
SSH_REMOTE_PORT="2222"
ADB_REMOTE_PORT="5556"
PHONE_SSH_PORT="2222"
PHONE_ADB_PORT="5555"
KEEPALIVE_SEC="45"
BACKOFF_INITIAL_SEC="2"
BACKOFF_MAX_SEC="60"
USER_NAME="phonetunnel"
BUNDLE=""

usage() {
    cat << 'EOF'
usage: setup-phonetunnel-account.sh [options]

  --dry-run                 print actions, change nothing
  --root DIR                write under DIR (default /). DIR other than /
                            skips useradd and sshd reload.
  --cup-host HOST           host the phone connects to (default 62.83.35.197)
  --cup-port PORT           ssh port on that host (default 22)
  --ssh-port PORT           cup localhost port forwarded to the phone sshd (2222)
  --adb-port PORT           cup localhost port forwarded to phone adb (5556)
  --phone-ssh-port PORT     phone sshd bind port (2222)
  --phone-adb-port PORT     phone adb bind port (5555)
  --keepalive SEC           phone ServerAliveInterval written into config.env
  --backoff-initial SEC
  --backoff-max SEC
  --user NAME               unix account (default phonetunnel)
  --bundle DIR              where keys and the phone bundle are written
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1; shift ;;
        --root) ROOT_DIR=$2; shift 2 ;;
        --cup-host) CUP_HOST=$2; shift 2 ;;
        --cup-port) CUP_PORT=$2; shift 2 ;;
        --ssh-port) SSH_REMOTE_PORT=$2; shift 2 ;;
        --adb-port) ADB_REMOTE_PORT=$2; shift 2 ;;
        --phone-ssh-port) PHONE_SSH_PORT=$2; shift 2 ;;
        --phone-adb-port) PHONE_ADB_PORT=$2; shift 2 ;;
        --keepalive) KEEPALIVE_SEC=$2; shift 2 ;;
        --backoff-initial) BACKOFF_INITIAL_SEC=$2; shift 2 ;;
        --backoff-max) BACKOFF_MAX_SEC=$2; shift 2 ;;
        --user) USER_NAME=$2; shift 2 ;;
        --bundle) BUNDLE=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOLD_SRC="$SCRIPT_DIR/phonetunnel-hold.sh"

dest_path() {
    local rel=$1
    case "$rel" in
        /*) ;;
        *) echo "dest_path expects an absolute path" >&2; exit 1 ;;
    esac
    if [ "$ROOT_DIR" = "/" ]; then
        printf '%s\n' "$rel"
    else
        printf '%s%s\n' "${ROOT_DIR%/}" "$rel"
    fi
}

say() {
    printf '%s\n' "$*"
}

if [ -z "$BUNDLE" ]; then
    BUNDLE="$(dest_path /root/phonetunnel-install)"
fi

SSHD_CONFIG="$(dest_path /etc/ssh/sshd_config)"
HOLD_DST="$(dest_path /usr/local/sbin/phonetunnel-hold)"
HOME_DIR="$(dest_path "/home/${USER_NAME}")"
AUTH_KEYS="${HOME_DIR}/.ssh/authorized_keys"
HOST_PUB_DIR="$(dest_path /etc/ssh)"

num_ok() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    return 0
}

for n in "$CUP_PORT" "$SSH_REMOTE_PORT" "$ADB_REMOTE_PORT" "$PHONE_SSH_PORT" \
    "$PHONE_ADB_PORT" "$KEEPALIVE_SEC" "$BACKOFF_INITIAL_SEC" "$BACKOFF_MAX_SEC"; do
    num_ok "$n" || { echo "not a port or number: $n" >&2; exit 2; }
done

case "$CUP_HOST" in
    *[!A-Za-z0-9.-]*) echo "bad host: $CUP_HOST" >&2; exit 2 ;;
esac
case "$USER_NAME" in
    *[!A-Za-z0-9._-]*) echo "bad user: $USER_NAME" >&2; exit 2 ;;
esac

if [ ! -f "$HOLD_SRC" ]; then
    echo "missing $HOLD_SRC" >&2
    exit 1
fi

if [ ! -f "$SSHD_CONFIG" ]; then
    echo "sshd config not found: $SSHD_CONFIG" >&2
    exit 1
fi

APPLY_SYSTEM=0
if [ "$ROOT_DIR" = "/" ]; then
    APPLY_SYSTEM=1
fi

if [ "$DRY" -eq 0 ] && [ "$APPLY_SYSTEM" -eq 1 ] && [ "$(id -u)" -ne 0 ]; then
    echo "refusing: real cup setup must run as root (or pass --dry-run)" >&2
    exit 1
fi

# nologin does not run ForceCommand. sshd execs "$SHELL -c <forcecommand>",
# and nologin ignores -c, prints "not available", and exits. /bin/sh runs
# the hold script. The hold script is what refuses a command. -N forwards
# do not open a session, so they never run it.
SHELL_BIN=""
for _sh in /bin/sh /usr/bin/sh; do
    if [ -x "$_sh" ]; then
        SHELL_BIN=$_sh
        break
    fi
done
if [ -z "$SHELL_BIN" ]; then
    echo "no /bin/sh to run ForceCommand" >&2
    exit 1
fi

# OpenSSH treats a shadow password starting with ! as a locked account
# and refuses public-key logins. '*' is "no password" without the lock.
# Checked on OpenSSH 9.6: the first matching Match keyword wins, so this
# block is inserted before any other Match and then re-read with sshd -T.

match_block() {
    cat << EOF
# BEGIN gsm2computer-phonetunnel
# Managed by scripts/setup-phonetunnel-account.sh. Do not edit by hand.
Match User ${USER_NAME}
    AllowTcpForwarding remote
    AllowStreamLocalForwarding no
    GatewayPorts no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitOpen none
    PermitListen 127.0.0.1:${SSH_REMOTE_PORT} 127.0.0.1:${ADB_REMOTE_PORT}
    ForceCommand ${HOLD_DST}
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PubkeyAuthentication yes
# END gsm2computer-phonetunnel
EOF
}

if [ "$DRY" -eq 1 ]; then
    say "dry-run: would install $HOLD_SRC -> $HOLD_DST"
    say "dry-run: would ensure user $USER_NAME shell=$SHELL_BIN shadow='*' (only when --root /)"
    say "dry-run: would insert Match before any other Match in $SSHD_CONFIG"
    say "dry-run: would refuse to continue if sshd -T for $USER_NAME is looser than that Match"
    say "dry-run: would generate or reuse keys in $BUNDLE"
    say "dry-run: would install a restricted authorized_keys at $AUTH_KEYS"
    if [ "$APPLY_SYSTEM" -eq 1 ]; then
        say "dry-run: would run sshd -t and reload ssh. Not done."
    else
        say "dry-run: would not useradd and would not reload sshd (--root is not /)"
    fi
    # Prove we did not touch the config.
    exit 0
fi

install -d -m 755 "$(dirname "$HOLD_DST")"
install -m 755 "$HOLD_SRC" "$HOLD_DST"

if [ "$APPLY_SYSTEM" -eq 1 ]; then
    if ! id "$USER_NAME" >/dev/null 2>&1; then
        useradd --system --create-home --home-dir "/home/${USER_NAME}" --shell "$NL" "$USER_NAME"
    fi
    usermod -s "$SHELL_BIN" "$USER_NAME"
    # shellcheck disable=SC2016
    usermod -p '*' "$USER_NAME"
    chmod 750 "/home/${USER_NAME}"
fi

install -d -m 700 "$BUNDLE"
# The phone client is Dropbear. It rejects OpenSSH private keys
# ("String too long") because that format's cipher header is not what
# dbclient's loader expects. dropbearkey writes a key dbclient can read.
# The shell key stays OpenSSH format: cup's ssh client uses it.
if ! command -v dropbearkey >/dev/null 2>&1; then
    echo "dropbearkey is required (Debian/Ubuntu package dropbear-bin)." >&2
    echo "The phone tunnel client cannot read an OpenSSH private key." >&2
    exit 1
fi
if [ ! -f "$BUNDLE/id_ed25519" ]; then
    dropbearkey -t ed25519 -f "$BUNDLE/id_ed25519" > "$BUNDLE/id_ed25519.txt"
fi
dropbearkey -y -f "$BUNDLE/id_ed25519" | awk '/^ssh-ed25519 / {print $1, $2; exit}' > "$BUNDLE/id_ed25519.pub"
if [ ! -s "$BUNDLE/id_ed25519.pub" ]; then
    echo "dropbearkey -y did not print an ssh-ed25519 public key" >&2
    exit 1
fi
if [ ! -f "$BUNDLE/phone_shell_ed25519" ]; then
    ssh-keygen -t ed25519 -f "$BUNDLE/phone_shell_ed25519" -N "" -C "phonetunnel-shell"
fi
chmod 600 "$BUNDLE/id_ed25519" "$BUNDLE/phone_shell_ed25519"
chmod 644 "$BUNDLE/id_ed25519.pub" "$BUNDLE/phone_shell_ed25519.pub"

TUNNEL_PUB=$(awk '{print $1" "$2}' "$BUNDLE/id_ed25519.pub")
TUNNEL_BLOB=$(awk '{print $2}' "$BUNDLE/id_ed25519.pub")
# permitopen is a single non-routable TEST-NET target so re-enabling
# port-forwarding cannot open a local (-L) forward to a real host.
# restrict does not disable unix-socket forwards; sshd_config does.
AUTH_LINE="restrict,port-forwarding,permitlisten=\"127.0.0.1:${SSH_REMOTE_PORT}\",permitlisten=\"127.0.0.1:${ADB_REMOTE_PORT}\",permitopen=\"192.0.2.1:9\" ${TUNNEL_PUB} phonetunnel-phone"

install -d -m 700 "${HOME_DIR}/.ssh"
if [ -f "$AUTH_KEYS" ]; then
    grep -F -v "$TUNNEL_BLOB" "$AUTH_KEYS" > "${AUTH_KEYS}.tmp" || true
else
    : > "${AUTH_KEYS}.tmp"
fi
printf '%s\n' "$AUTH_LINE" >> "${AUTH_KEYS}.tmp"
mv "${AUTH_KEYS}.tmp" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
if [ "$(id -u)" -eq 0 ] && id "$USER_NAME" >/dev/null 2>&1; then
    chown -R "${USER_NAME}:${USER_NAME}" "$HOME_DIR"
fi

# Dropbear known_hosts is "hostname algo base64" with no bracketed port
# and no hash. Pin every host key the stand-in or cup sshd offers.
KNOWN="$BUNDLE/known_hosts"
: > "$KNOWN"
found_hostkey=0
for pub in "$HOST_PUB_DIR"/ssh_host_*_key.pub; do
    [ -f "$pub" ] || continue
    found_hostkey=1
    algo=$(awk '{print $1}' "$pub")
    blob=$(awk '{print $2}' "$pub")
    printf '%s %s %s\n' "$CUP_HOST" "$algo" "$blob" >> "$KNOWN"
done
if [ "$found_hostkey" -ne 1 ]; then
    echo "no ssh host public keys in $HOST_PUB_DIR" >&2
    exit 1
fi
chmod 644 "$KNOWN"

SHELL_PUB=$(awk '{print $1" "$2}' "$BUNDLE/phone_shell_ed25519.pub")
# Options dropbear 2025.89 actually parses. Do not add OpenSSH-only
# words here; an unknown option makes dropbear ignore the whole key.
# no-pty would block the rescue shell. Forwarding stays off.
# A command without a TTY still works; ssh -t allocates a PTY.
printf '%s\n' "no-port-forwarding,no-agent-forwarding,no-X11-forwarding ${SHELL_PUB} phonetunnel-shell" \
    > "$BUNDLE/authorized_keys"
chmod 600 "$BUNDLE/authorized_keys"

cat > "$BUNDLE/config.env" << EOF
CUP_HOST=${CUP_HOST}
CUP_PORT=${CUP_PORT}
CUP_USER=${USER_NAME}
KEEPALIVE_SEC=${KEEPALIVE_SEC}
BACKOFF_INITIAL_SEC=${BACKOFF_INITIAL_SEC}
BACKOFF_MAX_SEC=${BACKOFF_MAX_SEC}
SSH_REMOTE_PORT=${SSH_REMOTE_PORT}
ADB_REMOTE_PORT=${ADB_REMOTE_PORT}
PHONE_SSH_PORT=${PHONE_SSH_PORT}
PHONE_ADB_PORT=${PHONE_ADB_PORT}
TS_PACKAGE=com.tailscale.ipn
TS_ACTIVITY=com.tailscale.ipn/com.tailscale.ipn.IPNActivity
TS_CHECK_SEC=60
TS_DOWN_STREAK=3
TS_RESTART_MIN_SEC=900
EOF
chmod 600 "$BUNDLE/config.env"

# First Match keyword wins. Appending this block loses to an earlier
# Match User / Match all that already set AllowTcpForwarding or PermitOpen.
# Insert before the first Match. A Match inside an Include that is
# processed first still wins; sshd -T below refuses that and restores.
BACKUP="${SSHD_CONFIG}.bak.phonetunnel"
cp -a "$SSHD_CONFIG" "$BACKUP"
BLOCK="${SSHD_CONFIG}.phonetunnel-block"
match_block > "$BLOCK"
awk '
    BEGIN {skip=0}
    $0=="# BEGIN gsm2computer-phonetunnel" {skip=1; next}
    $0=="# END gsm2computer-phonetunnel" {skip=0; next}
    skip==0 {print}
' "$SSHD_CONFIG" > "${SSHD_CONFIG}.stripped"
awk -v block="$BLOCK" '
    BEGIN {inserted=0}
    /^[[:space:]]*Match[[:space:]]/ && inserted==0 {
        while ((getline line < block) > 0) print line
        inserted=1
    }
    {print}
    END {
        if (inserted==0) {
            while ((getline line < block) > 0) print line
        }
    }
' "${SSHD_CONFIG}.stripped" > "${SSHD_CONFIG}.new"
mv "${SSHD_CONFIG}.new" "$SSHD_CONFIG"
rm -f "$BLOCK" "${SSHD_CONFIG}.stripped"

if [ -x /usr/sbin/sshd ]; then
    SSHD_BIN=/usr/sbin/sshd
else
    SSHD_BIN=sshd
fi

restore_sshd() {
    cp -a "$BACKUP" "$SSHD_CONFIG"
    echo "restored $SSHD_CONFIG; sshd was not reloaded" >&2
}

if ! "$SSHD_BIN" -t -f "$SSHD_CONFIG"; then
    restore_sshd
    echo "sshd -t failed." >&2
    exit 1
fi

EFFECTIVE=$("$SSHD_BIN" -T -C "user=${USER_NAME},host=127.0.0.1,addr=127.0.0.1" -f "$SSHD_CONFIG" 2>"${SSHD_CONFIG}.T.err") || {
    echo "sshd -T failed:" >&2
    cat "${SSHD_CONFIG}.T.err" >&2
    restore_sshd
    exit 1
}
rm -f "${SSHD_CONFIG}.T.err"

setting() {
    printf '%s\n' "$EFFECTIVE" | awk -v k="$1" '$1==k { $1=""; sub(/^ /,""); print; exit }'
}

fail_effective() {
    echo "effective sshd settings for ${USER_NAME} are not the tunnel policy." >&2
    echo "An earlier Match (often in sshd_config.d, included before this file) won." >&2
    echo "Fix that Match, then re-run. sshd was not reloaded." >&2
    printf '%s\n' "$EFFECTIVE" | awk '$1 ~ /^(allowtcpforwarding|allowstreamlocalforwarding|permitopen|permitlisten|permittty|x11forwarding|allowagentforwarding|gatewayports|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|forcecommand)$/ {print}' >&2
    restore_sshd
    exit 1
}

[ "$(setting allowtcpforwarding)" = "remote" ] || fail_effective
[ "$(setting allowstreamlocalforwarding)" = "no" ] || fail_effective
[ "$(setting permitopen)" = "none" ] || fail_effective
[ "$(setting permittty)" = "no" ] || fail_effective
[ "$(setting x11forwarding)" = "no" ] || fail_effective
[ "$(setting allowagentforwarding)" = "no" ] || fail_effective
[ "$(setting gatewayports)" = "no" ] || fail_effective
[ "$(setting passwordauthentication)" = "no" ] || fail_effective
[ "$(setting kbdinteractiveauthentication)" = "no" ] || fail_effective
[ "$(setting pubkeyauthentication)" = "yes" ] || fail_effective
[ "$(setting forcecommand)" = "$HOLD_DST" ] || fail_effective

got_listen=$(printf '%s\n' "$EFFECTIVE" | awk '$1=="permitlisten" { for (i=2;i<=NF;i++) print $i }' | sort -u)
want_listen=$(printf '%s\n' "127.0.0.1:${SSH_REMOTE_PORT}" "127.0.0.1:${ADB_REMOTE_PORT}" | sort)
if [ "$got_listen" != "$want_listen" ]; then
    echo "permitlisten is '$got_listen', want only the two localhost ports" >&2
    fail_effective
fi

if [ "$APPLY_SYSTEM" -eq 1 ]; then
    reloaded=0
    if command -v systemctl >/dev/null 2>&1; then
        if systemctl reload ssh; then
            reloaded=1
        elif systemctl reload sshd; then
            reloaded=1
        fi
    fi
    if [ "$reloaded" -eq 0 ] && command -v service >/dev/null 2>&1; then
        if service ssh reload; then
            reloaded=1
        fi
    fi
    if [ "$reloaded" -eq 0 ]; then
        echo "sshd config is valid but it was not reloaded. Reload ssh yourself." >&2
        exit 1
    fi
fi

cat << EOF

phonetunnel account is installed.
Bundle (root-only): ${BUNDLE}

Phone files to copy to /data/adb/phonetunnel/ on the Moto:
  id_ed25519              -> /data/adb/phonetunnel/id_ed25519
  known_hosts             -> /data/adb/phonetunnel/home/.ssh/known_hosts
  authorized_keys         -> /data/adb/phonetunnel/authorized_keys
  config.env              -> /data/adb/phonetunnel/config.env

Keep on cup:
  phone_shell_ed25519     ssh -i this -p ${SSH_REMOTE_PORT} root@127.0.0.1

After the phone has id_ed25519 and the tunnel stays up, delete the
phone private key from cup:
  shred -u ${BUNDLE}/id_ed25519

From cup, once the tunnel is up:
  ssh -i ${BUNDLE}/phone_shell_ed25519 -p ${SSH_REMOTE_PORT} \\
      -o UserKnownHostsFile=${BUNDLE}/phone_known_hosts \\
      root@127.0.0.1
  adb connect 127.0.0.1:${ADB_REMOTE_PORT}

The first shell login records the phone sshd host key. Check the
fingerprint against \`phonetunnel-status\` on the phone before trusting it.

EOF
