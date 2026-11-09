#!/system/bin/sh
# Early boot, after /data is mounted. Create the state directory only.
# The tunnel itself starts from service.sh once the network can come up.

MODDIR="${0%/*}"
mkdir -p /data/adb/phonetunnel
chmod 700 /data/adb/phonetunnel 2>/dev/null || true
# A previous install may have left skip_mount. This module has no
# system overlay we can afford to skip (status command lives there).
rm -f "$MODDIR/skip_mount"
