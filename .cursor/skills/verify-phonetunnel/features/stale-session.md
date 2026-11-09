# Stale cup session is reaped

## Sub-features

- The phonetunnel Match sets `ClientAliveInterval 15` and `ClientAliveCountMax 3`. OpenSSH sends one probe per interval and disconnects on the next interval after that many go unanswered, so a phone that vanishes without sending FIN is disconnected in about 60 seconds.
- Re-running setup replaces an existing Match block in place, including one that has no ClientAlive lines, and does not stack a second block.
- The phone client uses `ServerAliveInterval` and `ExitOnForwardFailure=yes`. A held remote port makes dbclient exit, and the supervisor retries with backoff until the port is free.
- The hold script does not kill sshd processes. `ssh -N` never runs it.

## Driving it with the stand-in harness

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-rebind
```

The drive does not need `launch`. It starts a private sshd on `10.88.0.1`, runs the phone supervisor inside a network namespace, drops that namespace's packets toward the sshd port, kills the phone processes, and starts a second supervisor on the host. The veth stays up so ClientAlive probes can be transmitted. The reverse port must still be held just after the kill. The second client must log `Remote TCP forward request failed` and then a shell nonce through the same port must succeed. Elapsed time from the vanish to that nonce is printed as `elapsed_sec` and must be between 30 and 90 seconds.

The drive does not connect to the reverse port while the old session is still established. sshd runs ClientAlive only when `ppoll` times out; a connection to the stale listener queues channel data, the socket stays writable, and the probe never runs.

`drive-account` checks that a Match block with the ClientAlive lines removed is rewritten to interval 15 and count 3, still as one block. `drive-policy` checks that an earlier Match setting `ClientAliveInterval 0` is refused and the file is left unchanged.

Evidence: `/opt/cursor/artifacts/verify-phonetunnel/rebind.txt`.

## Gotchas

- Deleting the namespace, or dropping the sshd's own probes, can free the port immediately or stall the server loop. The drive drops only what the phone sends, then kills the phone processes, and leaves the veth up.
- The stand-in used by `launch` passes `--client-alive-interval 4 --client-alive-count 2` so the shorter reconnect test stays fast. Production setup does not pass those flags. The defaults are 15 and 3.
- This is not a reboot of the Moto and not `62.83.35.197`. It is the same silence: the client is gone and cup sees no FIN.
