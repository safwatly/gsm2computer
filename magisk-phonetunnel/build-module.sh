#!/bin/bash
# Pack magisk-phonetunnel/ into phonetunnel-magisk.zip.
# The zip is what `magisk --install-module` installs. It contains no keys.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -f bin/dropbearmulti ]; then
    echo "bin/dropbearmulti is missing. Run ./build-binaries.sh first." >&2
    exit 1
fi

file bin/dropbearmulti | grep -q 'ARM aarch64' || {
    echo "bin/dropbearmulti is not an aarch64 binary" >&2
    exit 1
}
file bin/dropbearmulti | grep -q 'static-pie' || {
    echo "bin/dropbearmulti is not static-pie" >&2
    exit 1
}

chmod 755 bin/dropbearmulti bin/dropbear bin/dbclient bin/dropbearkey \
    bin/supervisor.sh bin/watchdog.sh bin/status.sh bin/ctl.sh \
    service.sh post-fs-data.sh customize.sh uninstall.sh \
    system/bin/phonetunnel-status system/bin/phonetunnel-ctl

OUT="$ROOT/phonetunnel-magisk.zip"
rm -f "$OUT"
zip -r "$OUT" \
    module.prop \
    customize.sh \
    post-fs-data.sh \
    service.sh \
    uninstall.sh \
    config.example.env \
    BINARIES.md \
    README.md \
    META-INF \
    bin \
    system \
    -x 'bin/dropbearmulti.bak' -x '*.DS_Store'

echo "wrote $OUT"
unzip -l "$OUT"
