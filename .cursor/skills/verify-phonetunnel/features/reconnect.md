# Tunnel recovers

## Sub-features

- If the `dbclient` process is killed, the supervisor dials again and the shell works.
- If packets to cup are black-holed, keepalive expiry ends the session and a later dial succeeds once the path is back.
- If cup's sshd restarts, the phone reconnects without a new module install.
- Backoff grows on fast failures and resets after a session that stayed up at least 60 seconds.

## How to get to it (user POV)

No extra command. Leave the module running. After a Wi-Fi to mobile
switch, a cup reboot, or Tailscale being irrelevant, wait for the
keepalive (about two minutes at the 45s production interval, three
misses) plus the backoff, then on cup:

```sh
ssh -i /root/phonetunnel-install/phone_shell_ed25519 -p 2222 root@127.0.0.1 echo ok
```

On the phone, `phonetunnel.log` shows `tunnel exited` and a later
`connecting` line. `phonetunnel-status` returns to `tunnel=up`.

## Driving it with the stand-in harness

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-reconnect
```

Three steps, each followed by a shell nonce:

1. `kill` the tunnel pid from the stand-in state directory.
2. `iptables -I OUTPUT -p tcp -d 127.0.0.1 --dport <stand-in ssh port> -j DROP`, wait until the tunnel pid changes or disappears, then delete the rule.
3. `kill` the stand-in sshd and start it again with the same config file.

The iptables rule is removed before the next step, and `cleanup` removes
it if the drive is interrupted. The drive exits non-zero if `iptables`
is missing.

Evidence: `/opt/cursor/artifacts/verify-phonetunnel/reconnect.txt`.

## Gotchas

- The stand-in keepalive is 2 seconds so the drop test finishes quickly.
  Production is 45 seconds. Do not copy the stand-in `config.env` to the phone.
- That drop still has a live client until its own keepalive expires.
  A reboot that leaves no FIN is `drive-rebind`: cup's Match
  `ClientAliveInterval 15` / `ClientAliveCountMax 3` reaps the orphan,
  and the phone's `ExitOnForwardFailure` retry binds the ports again.
- An iptables DROP on loopback is not a radio change. The phone path
  (Wi-Fi, mobile data, carrier NAT) is unverified.
- Killing the stand-in sshd is not a reboot of the Netcup VPS. Host
  keys stay the same in the test. A real cup reinstall that rotates
  host keys will stick until `known_hosts` on the phone is updated,
  because `StrictHostKeyChecking=yes`.
