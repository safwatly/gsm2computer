---
name: verify-phonetunnel
description: Verify the gsm2computer-phonetunnel Magisk module (outbound reverse SSH to cup, phone Dropbear sshd, adb forward, Tailscale watchdog) with the Linux stand-in harness. Use before declaring phonetunnel, reverse-tunnel, or phone-access-without-Tailscale work done. Does not reach the Moto or the cup VPS.
---

# Verify phonetunnel

The module under `magisk-phonetunnel/` is an outbound key-only SSH reverse
tunnel plus a localhost Dropbear sshd and a Tailscale watchdog. This skill
proves that behaviour on a Linux stand-in. It does not install a Magisk
module and it does not talk to the Moto or to `62.83.35.197`.

Read [features/README.md](features/README.md) before driving a feature.
The cup and phone procedure for a human is [docs/PHONETUNNEL.md](../../../docs/PHONETUNNEL.md).

## Launch

From the repo root:

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh launch
```

Ready when stdout contains `launch ok` and a `nonce=` line. That means
the private sshd accepted the phone key, the reverse shell port is open,
and a command through it echoed the nonce. The run directory is in
`/tmp/verify-phonetunnel-run`. `launch` replaces a previous stand-in.

Needs: `sudo`, `/usr/sbin/sshd`, `ssh`, `dropbearkey` (`dropbear-bin`),
`qemu-aarch64-static`, `python3`, `iptables` (for the reconnect drive).
The arm64 binary is `magisk-phonetunnel/bin/dropbearmulti`.

## Doctor

Read-only. Safe to run before launch and while a stand-in is up.

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh doctor
```

Worth driving when the output ends in `doctor ok`: the binary is aarch64
static-pie, qemu can execute `dbclient -h`, and
`scripts/setup-phonetunnel-account.sh --dry-run` leaves its scratch
`sshd_config` byte-for-byte unchanged and creates no key bundle.
If a stand-in is already up, doctor also prints `phonetunnel-status`.

## Drive

One feature:

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-tunnel
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-shell
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-adb
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-reconnect
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-supervise
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-watchdog
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-nopasswd
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-policy
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-account
```

All of them, in that order:

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-all
```

`drive-all` also checks that `/etc/ssh/sshd_config` still matches the
hash recorded at launch.

## Evidence

Each drive writes a file under `/opt/cursor/artifacts/verify-phonetunnel/`
(override with `PHONETUNNEL_EVIDENCE`):

| File | What it shows |
|---|---|
| `doctor.txt` | binary, qemu, dry-run side-effect check |
| `tunnel-up.txt` | `tunnel=up` only with sshd listening, host fingerprint, `phone_adb_managed=untouched`, a shell nonce, a refused extra `-R`, a refused `-L` data path |
| `phone-shell.txt` | a nonce from a command, shell key has no `no-pty`, pty result or the qemu tty limit |
| `adb-forward.txt` | `adb_reply=adb-standin-ok` through the cup adb port |
| `reconnect.txt` | shell works after killing the client, after an iptables drop, and after restarting the stand-in sshd |
| `supervise.txt` | dropbear restarts under the same tunnel pid; a dead dropbear makes `tunnel=down` while `tunnel_session=up` |
| `watchdog.txt` | streak restart, rate limit, call states, `ip` error, and a bridge package do not over-restart; `tun1` with a CGNAT address is up; carrier CGNAT alone is down; a ~250KB idle dump with historical `OFFHOOK`/`RINGING` still counts as idle under `mksh`, and a current precise foreground of `1` does not |
| `nopasswd.txt` | Dropbear accepts `root` when `/etc/passwd` has no root line, and rejects any other missing name |
| `policy.txt` | zero timers are raised, an earlier Include Match is refused and the config restored, key and Match block the extra `-R`, `-L`, and unix-socket forward, and ForceCommand runs |
| `account.txt` | real `useradd` in a private mount namespace: a loose Match writes nothing, a failure after the hold script rolls the new user back, a fresh account has shell `/bin/sh`, and a second run reuses the key. Host passwd is unchanged. Uninstall does not kill a reused pid |
| `phonetunnel.log` | supervisor log copied at the end of `drive-all` |
| `system-sshd.txt` | the system sshd config was not edited |
| `drive-all.txt` | `drive-all ok` |

A passing drive greps its own output for the nonce or the expected
`am` lines and exits non-zero if they are missing. Do not weaken a
check to make it pass. If a precondition is absent (no `iptables`, no
qemu), the harness exits and the feature stays unverified.

## Cleanup

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh cleanup
```

Kills the stand-in sshd, the supervisor, dropbear, the watchdog, the
adb banner, and any process whose command line contains the run
directory. Removes an iptables OUTPUT DROP if one was left behind.
Restores `/home/phonetunnel/.ssh/authorized_keys` if the harness
replaced it, or deletes the file if the harness created it. Deletes the
run directory. Does not delete `/opt/cursor/artifacts/verify-phonetunnel`
and does not edit `/etc/ssh/sshd_config`.

## Helpers

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh \
  doctor|launch|drive-tunnel|drive-shell|drive-adb|drive-reconnect|drive-supervise|drive-watchdog|drive-policy|drive-account|drive-all|cleanup
```

Cup account setup, used by `launch` with `--root` so it does not touch
the system sshd:

```sh
scripts/setup-phonetunnel-account.sh --help
```

Status, same script the phone runs, pointed at the stand-in state:

```sh
run=$(cat /tmp/verify-phonetunnel-run)
PHONETUNNEL_STATE="$run/state" \
  PHONETUNNEL_MODDIR="$PWD/magisk-phonetunnel" \
  PHONETUNNEL_CONFIG="$run/state/config.env" \
  sh magisk-phonetunnel/bin/status.sh
```

## What the stand-in does that the Moto will not

These stayed unverified on the device. Check them on the phone while
the line is idle, after the real cup setup in `docs/PHONETUNNEL.md`.

- Cup is a private sshd on `127.0.0.1` at a high port, not
  `62.83.35.197:22`. `/etc/ssh/sshd_config` is not edited and the system
  ssh service is not reloaded.
- A local user `phonetunnel` is created or adjusted (shell `/bin/sh`,
  shadow `*`) and `authorized_keys` is copied into `/home/phonetunnel`
  because OpenSSH StrictModes rejects a key file under `/tmp`.
  `drive-account` runs the real installer in a private mount namespace:
  it refuses an existing password or non-tunnel account unless
  `--adopt-existing` is passed, restores that account's shadow and home
  mode when a later step fails, and keeps extra bundle files such as
  `phone_known_hosts`.
- The supervisor is started with `sh` by the harness, not by Magisk
  `service.sh` at boot. There is no module zip install and no `remove`
  file plus reboot.
- `magisk-phonetunnel/bin/dropbearmulti` runs under
  `qemu-aarch64-static`, not on the Moto CPU and not under Android
  SELinux or Magisk labels.
- The shell through the tunnel is the harness uid and `/bin/sh`.
  `drive-nopasswd` logs in as `root` with uid 0 while the mounted
  passwd has no root line. This VM has no `/system/bin/sh`, so that
  test uses `/bin/sh`. The phone shell is `/system/bin/sh`. A PTY was
  not established: qemu-user
  fails `ioctl(TIOCSCTTY)` and `/dev/pts/0`. The key does not set
  `no-pty`. `ssh -t` on the phone is still unchecked.
- Adb is a Python process that answers `adb-probe` with
  `adb-standin-ok`. It is not `adbd`. There is no `adb connect`, no RSA
  prompt, no `/data/misc/adb/adb_keys`, and no `ro.adb.secure`. The
  module scripts do not start adbd; that choice is untested on a real
  adbd.
- The watchdog uses fake `ip`, `am`, and `dumpsys` on `PATH`. It does
  not start `com.tailscale.ipn`. Toybox `ip`, real `dumpsys` output, Doze,
  and the radio were not exercised.
- The network cut is `iptables` OUTPUT DROP to the stand-in ssh port on
  loopback, not a Wi-Fi to mobile switch and not carrier NAT.
- The cup reboot is kill and restart of that private sshd. `sshd -T`
  in `drive-policy` reads that private config, not cup's live sshd.
- The phone sshd key directory is `~/.phonetunnel-keys` because
  `getprop` is missing. On the phone it is `/dev/phonetunnel-keys`.
- Call-idle is a text file the fake `dumpsys` prints. The bridge app is
  not installed. Battery, Doze, and radio data were not measured.
- Keepalive is 2 seconds and backoff caps at 4 seconds. Production
  `config.env` is 45 seconds and 2..60 seconds.
- `dropbearkey` from the VM package `dropbear-bin` creates the tunnel
  private key. The phone does not run that x86_64 binary.
