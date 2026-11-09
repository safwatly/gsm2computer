# Tunnel comes up

## Sub-features

- Phone Dropbear listens on localhost only.
- `dbclient` authenticates to cup with the Dropbear private key and pins the host key.
- Cup localhost gains exactly two listeners, the shell port and the adb port.
- A reverse forward to any other port is refused.
- A local (`-L`) forward's data path is refused.
- `phonetunnel-status` reports `tunnel=up` only when the session is alive and the phone sshd is listening, plus `host_fingerprint=SHA256:...` and `phone_adb_managed=untouched`.

## How to get to it (user POV)

On cup, as root, `scripts/setup-phonetunnel-account.sh` has been run and
the four phone files are in `/data/adb/phonetunnel/`. The module is
installed and the phone has rebooted while the line was idle.

On the phone:

```sh
su -c phonetunnel-status
```

`tunnel=up`, `phone_sshd=listening`, `identity=present`.

On cup:

```sh
ss -ltn | grep -E '127.0.0.1:2222|127.0.0.1:5556'
```

Both lines are listen sockets owned by the phone's ssh session.

## Driving it with the stand-in harness

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh launch
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-tunnel
```

`launch` is the ready check: it waits until the reverse shell port
accepts a connection and a nonce comes back. `drive-tunnel` prints
status, echoes a nonce through the shell port, asks for a reverse
forward to port 39999 (must exit non-zero), and opens a local forward
then writes to it (the probe or the client log must show a reset or
"administratively prohibited").

Evidence: `/opt/cursor/artifacts/verify-phonetunnel/tunnel-up.txt`.

## Gotchas

- The phone identity has to be a `dropbearkey` file. An OpenSSH private
  key makes `dbclient` exit `String too long` before it connects, and
  the supervisor will not dial with that file.
- `PermitListen` is compared as a string. The client must request
  `127.0.0.1`, not `localhost` and not `0.0.0.0`.
- `ExitOnForwardFailure` does not fail a `-L` whose local bind
  succeeds. The refusal shows up when something connects to that port.
- On the phone the sshd `authorized_keys` directory is
  `/dev/phonetunnel-keys` because `/data` is group-writable and Dropbear
  rejects that. The stand-in uses `~/.phonetunnel-keys` instead.
