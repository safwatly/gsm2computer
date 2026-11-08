#!/system/bin/sh
# Sourced by Magisk after the zip is extracted. Do not exit 0; Magisk
# continues the install after this file returns.

ui_print "- gsm2computer-phonetunnel"
ui_print "- Separate from gsm2computer-bridge. This module does not touch it."
ui_print "- Keys and config stay in /data/adb/phonetunnel (not inside the zip)."
ui_print "- Reboot only while the phone line is idle."

set_perm_recursive "$MODPATH/bin" 0 0 0755 0755
if [ -d "$MODPATH/system/bin" ]; then
    set_perm_recursive "$MODPATH/system/bin" 0 0 0755 0755
fi
