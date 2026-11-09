#!/system/bin/sh
# Magisk runs this when the module is removed (the remove file + reboot).
# Stop only this module's processes. Do not stop the bridge app.
# Do not set or clear the adb TCP properties. This module never writes
# them, and clearing them would drop whatever adb listener the operator
# already had.
#
# A pidfile can outlive its process. Killing whatever now owns that pid
# can hit an unrelated program, so the cmdline has to name this module.

STATE="${PHONETUNNEL_STATE:-/data/adb/phonetunnel}"

pid_is_ours() {
    _pid=$1
    _name=$2
    if [ ! -r "/proc/${_pid}/cmdline" ]; then
        return 1
    fi
    _cmd=$(tr '\0' ' ' < "/proc/${_pid}/cmdline" 2>/dev/null || true)
    case "$_name" in
        supervisor) printf '%s\n' "$_cmd" | grep -q 'supervisor.sh' ;;
        watchdog) printf '%s\n' "$_cmd" | grep -q 'watchdog.sh' ;;
        tunnel) printf '%s\n' "$_cmd" | grep -q 'dbclient' ;;
        dropbear) printf '%s\n' "$_cmd" | grep -q 'dropbear' ;;
        *) return 1 ;;
    esac
}

for _name in supervisor tunnel dropbear watchdog; do
    _pidfile="$STATE/${_name}.pid"
    if [ ! -f "$_pidfile" ]; then
        continue
    fi
    _pid=$(cat "$_pidfile" 2>/dev/null || true)
    if [ -n "$_pid" ] && pid_is_ours "$_pid" "$_name"; then
        kill "$_pid" 2>/dev/null || true
    fi
    rm -f "$_pidfile"
done
echo "phonetunnel: removed. adb properties were not changed. keys remain in $STATE" >> "$STATE/phonetunnel.log" 2>/dev/null || true
