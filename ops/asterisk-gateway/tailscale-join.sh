#!/bin/sh
# Tailscale join is a manual step. This script does not take an auth key and
# does not call `tailscale up`. Run it only as a reminder.
echo "Join this Pi to the tailnet yourself, from a root shell on the Pi:"
echo "  curl -fsSL https://tailscale.com/install.sh | sh"
echo "  tailscale up --hostname=gsm-gateway --ssh"
echo "Do not put an auth key in the repo or in this script."
echo "Then set GSM2COMPUTER_ASTERISK_AMI_HOST on cup to this Pi's tailscale IP"
echo "(tailscale ip -4)."
exit 1
