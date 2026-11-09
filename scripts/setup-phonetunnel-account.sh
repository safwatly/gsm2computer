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
ADOPT=0

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
  --adopt-existing          take over an existing account that is not already
                            this tunnel (has a usable password, or another shell).
                            Without this flag that account is left unchanged.
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
        --adopt-existing) ADOPT=1; shift ;;
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

# Nothing under the live paths is written until sshd -T accepts a staged
# config. A failure before that commit leaves the system untouched. A
# failure after it rolls back the user, hold script, key bundle, and
# sshd config this run changed, so a second run starts clean. An
# existing account also gets its shell, shadow field, and home mode back.
STAGE=$(mktemp -d)
STAGE_BUNDLE="$STAGE/bundle"
STAGE_SSHD="$STAGE/sshd_config"
USER_CREATED=0
USER_HAD_PREV=0
PREV_SHELL=""
PREV_HOME=""
PREV_MODE=""
PREV_OWNER=""
SSHD_TMP=""
HOLD_COMMITTED=0
HOLD_HAD_PREV=0
BUNDLE_COMMITTED=0
BUNDLE_HAD_PREV=0
AUTH_COMMITTED=0
AUTH_HAD_PREV=0
AUTH_DOTSSH_MODE=""
AUTH_DOTSSH_OWNER=""
SSHD_COMMITTED=0

remove_stage() {
    rm -rf "$STAGE"
}

rollback_commit() {
    local rc=$?
    trap - EXIT
    echo "phonetunnel setup failed; rolling back this run" >&2
    if [ -n "$SSHD_TMP" ]; then
        rm -f "$SSHD_TMP"
    fi
    if [ "$SSHD_COMMITTED" -eq 1 ] && [ -f "$STAGE/sshd.prev" ]; then
        cp -a "$STAGE/sshd.prev" "$SSHD_CONFIG"
        echo "restored $SSHD_CONFIG; sshd was not reloaded" >&2
    fi
    if [ "$HOLD_COMMITTED" -eq 1 ]; then
        if [ "$HOLD_HAD_PREV" -eq 1 ]; then
            cp -a "$STAGE/hold.prev" "$HOLD_DST"
        else
            rm -f "$HOLD_DST"
        fi
    fi
    if [ "$BUNDLE_COMMITTED" -eq 1 ]; then
        rm -rf "$BUNDLE"
        if [ "$BUNDLE_HAD_PREV" -eq 1 ]; then
            mv "$STAGE/bundle.prev" "$BUNDLE"
        fi
    fi
    if [ "$AUTH_COMMITTED" -eq 1 ] && [ "$USER_CREATED" -eq 0 ]; then
        if [ "$AUTH_HAD_PREV" -eq 1 ]; then
            cp -a "$STAGE/auth.prev" "$AUTH_KEYS"
        else
            rm -f "$AUTH_KEYS"
            if [ -z "$AUTH_DOTSSH_MODE" ]; then
                rmdir "${HOME_DIR}/.ssh" >/dev/null 2>&1 || true
            fi
        fi
        if [ -n "$AUTH_DOTSSH_MODE" ] && [ -d "${HOME_DIR}/.ssh" ]; then
            chmod "$AUTH_DOTSSH_MODE" "${HOME_DIR}/.ssh" >/dev/null 2>&1 || true
        fi
        if [ -n "$AUTH_DOTSSH_OWNER" ] && [ -d "${HOME_DIR}/.ssh" ]; then
            chown "$AUTH_DOTSSH_OWNER" "${HOME_DIR}/.ssh" >/dev/null 2>&1 || true
        fi
    fi
    if [ "$USER_CREATED" -eq 1 ]; then
        userdel -r "$USER_NAME" >/dev/null 2>&1 || userdel "$USER_NAME" >/dev/null 2>&1 || true
    elif [ "$USER_HAD_PREV" -eq 1 ]; then
        if [ -n "$PREV_SHELL" ]; then
            usermod -s "$PREV_SHELL" "$USER_NAME" >/dev/null 2>&1 || true
        fi
        if [ -f "$STAGE/shadow.prev" ]; then
            usermod -p "$(cat "$STAGE/shadow.prev")" "$USER_NAME" >/dev/null 2>&1 || true
        fi
        if [ -n "$PREV_HOME" ] && [ -d "$PREV_HOME" ]; then
            if [ -n "$PREV_MODE" ]; then
                chmod "$PREV_MODE" "$PREV_HOME" >/dev/null 2>&1 || true
            fi
            if [ -n "$PREV_OWNER" ]; then
                chown "$PREV_OWNER" "$PREV_HOME" >/dev/null 2>&1 || true
            fi
        fi
    fi
    remove_stage
    exit "$rc"
}

trap remove_stage EXIT

maybe_fail() {
    if [ "${PHONETUNNEL_SETUP_FAIL_AFTER:-}" = "$1" ]; then
        echo "phonetunnel setup forced failure after $1" >&2
        exit 1
    fi
}

install -d -m 700 "$STAGE_BUNDLE"
# The phone client is Dropbear. It rejects OpenSSH private keys
# ("String too long") because that format's cipher header is not what
# dbclient's loader expects. dropbearkey writes a key dbclient can read.
# The shell key stays OpenSSH format: cup's ssh client uses it.
if ! command -v dropbearkey >/dev/null 2>&1; then
    echo "dropbearkey is required (Debian/Ubuntu package dropbear-bin)." >&2
    echo "The phone tunnel client cannot read an OpenSSH private key." >&2
    exit 1
fi
# Keep every file already in the bundle. A re-run used to copy only the
# two private keys, which dropped phone_known_hosts and anything else
# the operator had saved next to them.
if [ -d "$BUNDLE" ]; then
    cp -a "$BUNDLE/." "$STAGE_BUNDLE/"
fi
if [ ! -f "$STAGE_BUNDLE/id_ed25519" ]; then
    dropbearkey -t ed25519 -f "$STAGE_BUNDLE/id_ed25519" > "$STAGE_BUNDLE/id_ed25519.txt"
fi
dropbearkey -y -f "$STAGE_BUNDLE/id_ed25519" | awk '/^ssh-ed25519 / {print $1, $2; exit}' > "$STAGE_BUNDLE/id_ed25519.pub"
if [ ! -s "$STAGE_BUNDLE/id_ed25519.pub" ]; then
    echo "dropbearkey -y did not print an ssh-ed25519 public key" >&2
    exit 1
fi
if [ ! -f "$STAGE_BUNDLE/phone_shell_ed25519" ]; then
    ssh-keygen -t ed25519 -f "$STAGE_BUNDLE/phone_shell_ed25519" -N "" -C "phonetunnel-shell"
fi
# The public half is derived from the private key so a re-run does not
# depend on the .pub file still being in the bundle.
ssh-keygen -y -f "$STAGE_BUNDLE/phone_shell_ed25519" > "$STAGE_BUNDLE/phone_shell_ed25519.pub"
chmod 600 "$STAGE_BUNDLE/id_ed25519" "$STAGE_BUNDLE/phone_shell_ed25519"
chmod 644 "$STAGE_BUNDLE/id_ed25519.pub" "$STAGE_BUNDLE/phone_shell_ed25519.pub"

TUNNEL_PUB=$(awk '{print $1" "$2}' "$STAGE_BUNDLE/id_ed25519.pub")
TUNNEL_BLOB=$(awk '{print $2}' "$STAGE_BUNDLE/id_ed25519.pub")
# permitopen is a single non-routable TEST-NET target so re-enabling
# port-forwarding cannot open a local (-L) forward to a real host.
# restrict does not disable unix-socket forwards; sshd_config does.
AUTH_LINE="restrict,port-forwarding,permitlisten=\"127.0.0.1:${SSH_REMOTE_PORT}\",permitlisten=\"127.0.0.1:${ADB_REMOTE_PORT}\",permitopen=\"192.0.2.1:9\" ${TUNNEL_PUB} phonetunnel-phone"

STAGE_AUTH="$STAGE/authorized_keys"
if [ -f "$AUTH_KEYS" ]; then
    grep -F -v "$TUNNEL_BLOB" "$AUTH_KEYS" > "$STAGE_AUTH" || true
else
    : > "$STAGE_AUTH"
fi
printf '%s\n' "$AUTH_LINE" >> "$STAGE_AUTH"
chmod 600 "$STAGE_AUTH"

# Dropbear known_hosts is "hostname algo base64" with no bracketed port
# and no hash. Pin every host key the stand-in or cup sshd offers.
KNOWN="$STAGE_BUNDLE/known_hosts"
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

SHELL_PUB=$(awk '{print $1" "$2}' "$STAGE_BUNDLE/phone_shell_ed25519.pub")
# Options dropbear 2025.89 actually parses. Do not add OpenSSH-only
# words here; an unknown option makes dropbear ignore the whole key.
# no-pty would block the rescue shell. Forwarding stays off.
# A command without a TTY still works; ssh -t allocates a PTY.
printf '%s\n' "no-port-forwarding,no-agent-forwarding,no-X11-forwarding ${SHELL_PUB} phonetunnel-shell" \
    > "$STAGE_BUNDLE/authorized_keys"
chmod 600 "$STAGE_BUNDLE/authorized_keys"

cat > "$STAGE_BUNDLE/config.env" << EOF
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
chmod 600 "$STAGE_BUNDLE/config.env"

# First Match keyword wins. Build the candidate in the stage directory
# and accept it with sshd -T before replacing the live file. An Include
# that already set AllowTcpForwarding still wins; that failure happens
# before any account, hold script, or key is installed.
BLOCK="$STAGE/match.block"
match_block > "$BLOCK"
awk '
    BEGIN {skip=0}
    $0=="# BEGIN gsm2computer-phonetunnel" {skip=1; next}
    $0=="# END gsm2computer-phonetunnel" {skip=0; next}
    skip==0 {print}
' "$SSHD_CONFIG" > "$STAGE/sshd.stripped"
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
' "$STAGE/sshd.stripped" > "$STAGE_SSHD"

if [ -x /usr/sbin/sshd ]; then
    SSHD_BIN=/usr/sbin/sshd
else
    SSHD_BIN=sshd
fi

if ! "$SSHD_BIN" -t -f "$STAGE_SSHD"; then
    echo "sshd -t failed. Live sshd config was not changed." >&2
    exit 1
fi

EFFECTIVE=$("$SSHD_BIN" -T -C "user=${USER_NAME},host=127.0.0.1,addr=127.0.0.1" -f "$STAGE_SSHD" 2>"$STAGE/sshd.T.err") || {
    echo "sshd -T failed. Live sshd config was not changed." >&2
    cat "$STAGE/sshd.T.err" >&2
    exit 1
}

setting() {
    printf '%s\n' "$EFFECTIVE" | awk -v k="$1" '$1==k { $1=""; sub(/^ /,""); print; exit }'
}

fail_effective() {
    echo "effective sshd settings for ${USER_NAME} are not the tunnel policy." >&2
    echo "An earlier Match (often in sshd_config.d, included before this file) won." >&2
    echo "Fix that Match, then re-run. sshd was not reloaded." >&2
    printf '%s\n' "$EFFECTIVE" | awk '$1 ~ /^(allowtcpforwarding|allowstreamlocalforwarding|permitopen|permitlisten|permittty|x11forwarding|allowagentforwarding|gatewayports|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|forcecommand)$/ {print}' >&2
    echo "Live sshd config was not changed." >&2
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

# A shadow field can log in when it is not locked. '*' and '!' (and
# '!!', '!*') are locks. An empty field is a usable empty password.
# Anything else, including a crypt hash, is usable.
shadow_unusable() {
    case "$1" in
        '*'|'!'|'!!') return 0 ;;
        '!'*) return 0 ;;
    esac
    return 1
}

# Our own re-run: shell is /bin/sh, no usable password, and this user's
# Match block or tunnel key is already installed.
dedicated_tunnel_account() {
    local _shell _shadow
    _shell=$(getent passwd "$USER_NAME" | awk -F: '{print $7}')
    _shadow=$(awk -F: -v u="$USER_NAME" '$1==u {print $2; exit}' /etc/shadow)
    [ "$_shell" = "$SHELL_BIN" ] || return 1
    shadow_unusable "$_shadow" || return 1
    if awk -v u="$USER_NAME" '
        $0=="# BEGIN gsm2computer-phonetunnel" {inside=1}
        inside && $0==("Match User " u) {found=1}
        $0=="# END gsm2computer-phonetunnel" {inside=0}
        END {exit found ? 0 : 1}
    ' "$SSHD_CONFIG"; then
        return 0
    fi
    if [ -f "$AUTH_KEYS" ] && grep -q 'phonetunnel-phone' "$AUTH_KEYS"; then
        return 0
    fi
    return 1
}

# Refuse before the commit trap. A refusal must not roll anything back
# and must not mark the account as one this run changed.
if [ "$APPLY_SYSTEM" -eq 1 ] && id "$USER_NAME" >/dev/null 2>&1; then
    if [ "$ADOPT" -ne 1 ] && ! dedicated_tunnel_account; then
        echo "refusing to adopt existing account ${USER_NAME}." >&2
        echo "It has a usable password or is not already this tunnel account." >&2
        echo "The account was not changed. Pass --adopt-existing to take it over." >&2
        exit 1
    fi
fi

# Commit. From here a failure undoes this run.
trap rollback_commit EXIT

if [ "$APPLY_SYSTEM" -eq 1 ]; then
    if id "$USER_NAME" >/dev/null 2>&1; then
        USER_HAD_PREV=1
        PREV_SHELL=$(getent passwd "$USER_NAME" | awk -F: '{print $7}')
        PREV_HOME=$(getent passwd "$USER_NAME" | awk -F: '{print $6}')
        awk -F: -v u="$USER_NAME" '$1==u {print $2; exit}' /etc/shadow > "$STAGE/shadow.prev"
        if [ -n "$PREV_HOME" ] && [ -d "$PREV_HOME" ]; then
            PREV_MODE=$(stat -c %a "$PREV_HOME")
            PREV_OWNER=$(stat -c %u:%g "$PREV_HOME")
        fi
    else
        useradd --system --create-home --home-dir "/home/${USER_NAME}" --shell "$SHELL_BIN" "$USER_NAME"
        USER_CREATED=1
        PREV_HOME="/home/${USER_NAME}"
    fi
    usermod -s "$SHELL_BIN" "$USER_NAME"
    # shellcheck disable=SC2016
    usermod -p '*' "$USER_NAME"
    if [ -n "$PREV_HOME" ] && [ -d "$PREV_HOME" ]; then
        chmod 750 "$PREV_HOME"
    fi
    maybe_fail user
fi

if [ -f "$HOLD_DST" ]; then
    cp -a "$HOLD_DST" "$STAGE/hold.prev"
    HOLD_HAD_PREV=1
fi
HOLD_COMMITTED=1
install -d -m 755 "$(dirname "$HOLD_DST")"
install -m 755 "$HOLD_SRC" "$HOLD_DST"
maybe_fail hold

if [ -d "$BUNDLE" ]; then
    cp -a "$BUNDLE" "$STAGE/bundle.prev"
    BUNDLE_HAD_PREV=1
fi
BUNDLE_COMMITTED=1
rm -rf "$BUNDLE"
install -d -m 700 "$(dirname "$BUNDLE")"
cp -a "$STAGE_BUNDLE" "$BUNDLE"
maybe_fail keys

if [ -f "$AUTH_KEYS" ]; then
    cp -a "$AUTH_KEYS" "$STAGE/auth.prev"
    AUTH_HAD_PREV=1
fi
if [ -d "${HOME_DIR}/.ssh" ]; then
    AUTH_DOTSSH_MODE=$(stat -c %a "${HOME_DIR}/.ssh")
    AUTH_DOTSSH_OWNER=$(stat -c %u:%g "${HOME_DIR}/.ssh")
fi
install -d -m 700 "${HOME_DIR}/.ssh"
install -m 600 "$STAGE_AUTH" "$AUTH_KEYS"
if [ "$(id -u)" -eq 0 ] && id "$USER_NAME" >/dev/null 2>&1; then
    if [ "$USER_CREATED" -eq 1 ]; then
        chown -R "${USER_NAME}:${USER_NAME}" "$HOME_DIR"
    else
        # Do not chown the rest of an existing home. Only the key we wrote.
        chown "${USER_NAME}:${USER_NAME}" "${HOME_DIR}/.ssh" "$AUTH_KEYS"
    fi
fi
AUTH_COMMITTED=1

cp -a "$SSHD_CONFIG" "$STAGE/sshd.prev"
SSHD_COMMITTED=1
# Write the new config beside the live file and rename it into place.
# A kill during the copy leaves the previous file intact.
SSHD_TMP="${SSHD_CONFIG}.tmp.$$"
cp "$STAGE_SSHD" "$SSHD_TMP"
chmod --reference="$SSHD_CONFIG" "$SSHD_TMP"
chown --reference="$SSHD_CONFIG" "$SSHD_TMP"
mv -f "$SSHD_TMP" "$SSHD_CONFIG"
SSHD_TMP=""
if ! "$SSHD_BIN" -t -f "$SSHD_CONFIG"; then
    echo "sshd -t failed on the installed config." >&2
    exit 1
fi
maybe_fail sshd

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
        echo "sshd reload failed. This run was rolled back." >&2
        exit 1
    fi
fi

trap remove_stage EXIT

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
