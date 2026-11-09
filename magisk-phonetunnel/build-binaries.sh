#!/bin/bash
# Build the static aarch64 Dropbear multi-call binary shipped in this module.
# Output: magisk-phonetunnel/bin/dropbearmulti and BINARIES.md
#
# Provenance (also recorded in BINARIES.md):
#   Dropbear 2025.89
#   https://matt.ucc.asn.au/dropbear/releases/dropbear-2025.89.tar.bz2
#   Cross compiler: https://musl.cc/aarch64-linux-musl-cross.tgz
#     (GCC 11.2.1, musl, aarch64; the binary is static and does not
#     link Android's libc, so it runs on the phone and under qemu-user.)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VER="2025.89"
TARBALL_SHA256="0d1f7ca711cfc336dc8a85e672cab9cfd8223a02fe2da0a4a7aeb58c9e113634"
TARBALL_URL="https://matt.ucc.asn.au/dropbear/releases/dropbear-${VER}.tar.bz2"
TOOLCHAIN_URL="https://musl.cc/aarch64-linux-musl-cross.tgz"
CACHE="${PHONETUNNEL_CACHE:-${HOME}/.cache/gsm2computer-phonetunnel}"

mkdir -p "$CACHE" "$ROOT/bin"
cd "$CACHE"

if [ ! -f "dropbear-${VER}.tar.bz2" ]; then
    curl -fL --retry 3 -o "dropbear-${VER}.tar.bz2" "$TARBALL_URL"
fi
echo "${TARBALL_SHA256}  dropbear-${VER}.tar.bz2" | sha256sum -c -

if [ ! -x "$CACHE/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc" ]; then
    if [ ! -f aarch64-linux-musl-cross.tgz ]; then
        curl -fL --retry 3 -o aarch64-linux-musl-cross.tgz "$TOOLCHAIN_URL"
    fi
    tar -xzf aarch64-linux-musl-cross.tgz -C "$CACHE"
fi
TOOLCHAIN_SHA256="$(sha256sum aarch64-linux-musl-cross.tgz | awk '{print $1}')"
GCC="$CACHE/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc"
"$GCC" --version | head -1

rm -rf "$CACHE/dropbear-${VER}"
tar -xjf "dropbear-${VER}.tar.bz2" -C "$CACHE"
cd "$CACHE/dropbear-${VER}"
# Android has no /etc/passwd. The patch synthesizes uid 0 for "root"
# only, and only when getpwnam fails. localoptions.h turns it on.
patch -p1 < "$ROOT/patches/android-root-login.patch"
cp "$ROOT/localoptions.h" ./localoptions.h

export PATH="$CACHE/aarch64-linux-musl-cross/bin:${PATH}"
./configure \
    --host=aarch64-linux-musl \
    --enable-static \
    --disable-zlib \
    --disable-syslog \
    --disable-lastlog \
    --disable-utmp \
    --disable-utmpx \
    --disable-wtmp \
    --disable-wtmpx \
    --disable-pututline \
    --disable-pam \
    CC="$GCC" \
    CFLAGS="-Os" \
    LDFLAGS="-static"

make -j"$(nproc)" PROGRAMS="dropbear dbclient dropbearkey" MULTI=1
aarch64-linux-musl-strip dropbearmulti
cp dropbearmulti "$ROOT/bin/dropbearmulti"
chmod 755 "$ROOT/bin/dropbearmulti"

BIN_SHA256="$(sha256sum "$ROOT/bin/dropbearmulti" | awk '{print $1}')"
BIN_FILE="$(file "$ROOT/bin/dropbearmulti")"
GCC_VER="$("$GCC" --version | head -1)"

cat > "$ROOT/BINARIES.md" << EOF
# Phone tunnel binaries

The Magisk module ships one executable, \`bin/dropbearmulti\`. Wrappers
named \`dropbear\`, \`dbclient\`, and \`dropbearkey\` exec that file.
There is no separate OpenSSH binary on the phone. Cup uses the VPS's
own OpenSSH server.

## dropbearmulti

| | |
|---|---|
| Upstream | Dropbear ${VER} |
| Source | ${TARBALL_URL} |
| Source SHA-256 | \`${TARBALL_SHA256}\` |
| Local options | \`magisk-phonetunnel/localoptions.h\` (password auth compiled out, no motd, Android root account) |
| Android login | \`magisk-phonetunnel/patches/android-root-login.patch\` synthesizes \`root\` (uid 0, gid 0, shell \`/system/bin/sh\`) when \`getpwnam\` fails. Other names stay nonexistent. |
| Programs | \`dropbear\`, \`dbclient\`, \`dropbearkey\` (multi-call) |
| Cross compiler | ${TOOLCHAIN_URL} |
| Toolchain tarball SHA-256 | \`${TOOLCHAIN_SHA256}\` |
| Compiler | ${GCC_VER} |
| Configure | \`--host=aarch64-linux-musl --enable-static --disable-zlib --disable-syslog --disable-lastlog --disable-utmp --disable-utmpx --disable-wtmp --disable-wtmpx --disable-pututline --disable-pam\` |
| Link | static-pie musl (\`-static\`). \`file\` reports \`static-pie linked\`: no dynamic linker, not Android bionic. |
| Binary SHA-256 | \`${BIN_SHA256}\` |
| \`file\` | ${BIN_FILE} |

Rebuild with \`./magisk-phonetunnel/build-binaries.sh\`. The script
refuses a Dropbear tarball whose SHA-256 does not match the pin above.
The toolchain checksum is recorded after download so a later rebuild
can be compared; musl.cc does not publish a separate checksum file.

Why Dropbear: current dbclient implements \`ExitOnForwardFailure\`,
\`ServerAliveInterval\`, \`StrictHostKeyChecking\`, and \`BatchMode\`.
The server supports \`-D <dir>\` for \`authorized_keys\`, \`-j\`/\`-k\`
to disable forwarding, and a localhost bind. One static binary covers
the phone sshd, the tunnel client, and host-key generation.

Syslog is compiled out because Android has no syslog daemon. The
supervisor appends the process's stderr to
\`/data/adb/phonetunnel/phonetunnel.log\`.

## Key format

\`dbclient\` in this build cannot load an OpenSSH private key
(\`-----BEGIN OPENSSH PRIVATE KEY-----\`). The loader treats the cipher
name at the start of the openssh-key-v1 blob as a length and exits
with \`String too long\`. The cup setup script therefore generates the
phone identity with \`dropbearkey\` (Debian/Ubuntu package \`dropbear-bin\`).
The cup-to-phone shell key stays an OpenSSH key, because cup's OpenSSH
client reads it and the phone only stores the public line.

Do not replace \`/data/adb/phonetunnel/id_ed25519\` with a key from
\`ssh-keygen\`. The supervisor refuses that file and logs the reason.

## Android account

The phone has no \`/etc/passwd\`. Unpatched Dropbear rejects every login
with \`Login attempt for nonexistent user\`. This build, when
\`getpwnam("root")\` fails, uses uid 0, gid 0, shell \`/system/bin/sh\`
(or \`/bin/sh\` when that is the one that exists), and home
\`/data/adb/phonetunnel/home\` if that directory exists, otherwise \`/\`.
A name other than \`root\` is still rejected. Password authentication
stays compiled out.
EOF

echo "built $ROOT/bin/dropbearmulti"
echo "$BIN_FILE"
echo "$BIN_SHA256"
