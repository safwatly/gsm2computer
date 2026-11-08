#!/system/bin/sh
# Magisk late_start. Background the supervisor and return so boot is
# not held. Does not start, stop, or signal the bridge app.

MODDIR="${0%/*}"
mkdir -p /data/adb/phonetunnel
chmod 700 /data/adb/phonetunnel 2>/dev/null || true
export PHONETUNNEL_MODDIR="$MODDIR"
# shellcheck disable=SC2094
"$MODDIR/bin/supervisor.sh" >>/data/adb/phonetunnel/service.log 2>&1 &
