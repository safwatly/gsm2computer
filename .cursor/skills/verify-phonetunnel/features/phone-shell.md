# Shell through the tunnel

## Sub-features

- From cup, `ssh -p 2222 root@127.0.0.1` with `phone_shell_ed25519` runs a command on the phone.
- The phone sshd has no password auth, no TTY request required for a simple command, and no forwarding (`-j -k`).
- The tunnel account on cup cannot itself be used as a shell.

## How to get to it (user POV)

Tunnel is up (`tunnel-up`). On cup:

```sh
ssh -i /root/phonetunnel-install/phone_shell_ed25519 \
    -o IdentitiesOnly=yes \
    -p 2222 root@127.0.0.1 id
```

The first connection asks you to trust the phone Dropbear host key.
Compare it with the fingerprint in the phone log before accepting.

A command as the tunnel user on cup must not run a shell:

```sh
ssh -i /root/phonetunnel-install/id_ed25519 phonetunnel@127.0.0.1 id
```

That is refused (the hold script exits 1). After the phone is confirmed
up, `shred` that private key on cup; the command above then has no key.

## Driving it with the stand-in harness

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-shell
```

Uses the OpenSSH client and `phone_shell_ed25519` against the stand-in
reverse port, with `StrictHostKeyChecking=yes` and a known_hosts file
filled at launch. The remote command is `printf` of a nonce. The drive
fails if the nonce is not in the output.

Evidence: `/opt/cursor/artifacts/verify-phonetunnel/phone-shell.txt`.

## Gotchas

- The stand-in shell user is the harness uid, not `root`, and the shell
  is `/bin/sh`, not `/system/bin/sh`. `id` on the real phone should show
  `uid=0`.
- Dropbear's `authorized_keys` options are a short list
  (`no-port-forwarding`, `no-agent-forwarding`, `no-X11-forwarding`,
  `no-pty`, `restrict`, `command=`, `permitopen=`). An OpenSSH-only
  option makes Dropbear ignore the whole key.
- Do not pass Dropbear `-s`. Password auth is compiled out, so that
  flag is not in this binary.
