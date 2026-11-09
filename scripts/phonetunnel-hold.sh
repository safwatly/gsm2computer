#!/bin/sh
# Forced command for the cup user `phonetunnel`.
# ssh -N (the phone tunnel) never runs this: OpenSSH only invokes
# ForceCommand when the client opens a session. A client that asks for
# a command or a shell gets this process instead of a shell, and the
# requested command is ignored.
# An empty SSH_ORIGINAL_COMMAND is an interactive session with no
# command. Keep the process alive so the connection (and any forwards
# on that session) is not a shell. A non-empty command is refused.
# ssh -N never runs this script, so it cannot see the reverse-forward
# session and it does not kill sshd processes. A dead phone is reaped
# by ClientAliveInterval on the Match block.

logger -t phonetunnel "session client=${SSH_CLIENT:-unknown} orig=${SSH_ORIGINAL_COMMAND:-none}" 2>/dev/null || true

if [ -n "${SSH_ORIGINAL_COMMAND:-}" ]; then
    logger -t phonetunnel "rejected command" 2>/dev/null || true
    echo "phonetunnel: command refused" >&2
    exit 1
fi

# No command was requested. This is still not a shell.
echo "phonetunnel: no shell" >&2
trap 'exit 0' HUP INT TERM
while true; do
    sleep 3600
done
