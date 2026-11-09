#!/system/bin/sh
# Magisk runs this when the module is removed (the remove file + reboot).
# Stop only this module's processes. Do not stop the bridge app.
# Do not set or clear service.adb.tcp.port or persist.adb.tcp.port.
# This module never writes those, and clearing them would drop whatever
# adb listener the operator already had.

STATE=/data/adb/phonetunnel
for _name in supervisor tunnel dropbear watchdog; do
    _pidfile="$STATE/${_name}.pid"
    if [ -f "$_pidfile" ]; then
        _pid=$(cat "$_pidfile" 2>/dev/null || true)
        if [ -n "$_pid" ]; then
            kill "$_pid" 2>/dev/null || true
        fi
        rm -f "$_pidfile"
    fi
done
echo "phonetunnel: removed. adb properties were not changed. keys remain in $STATE" >> "$STATE/phonetunnel.log" 2>/dev/null || true
