# Phone tunnel to cup

When Tailscale on the Moto is down, the phone has no reachable address:
the carrier IP changes and Orange NAT does not allow inbound connections.
This path is the opposite direction. The phone opens an SSH session out
to cup's public address `62.83.35.197` port 22. Cup then uses two
localhost ports on that session:

| On cup | Reaches on the phone |
|---|---|
| `127.0.0.1:2222` | Dropbear sshd on `127.0.0.1:2222` |
| `127.0.0.1:5556` | adbd on `127.0.0.1:5555` |

The phone sshd is key-only and cannot forward. The cup account
`phonetunnel` can open only those two reverse forwards. It has no shell,
no TTY, no agent, and no other forwards.

The module id is `gsm2computer-phonetunnel`. It does not mount over or
edit `gsm2computer-bridge`. Install and uninstall it only while the GSM
line is idle.

## Cup setup

On cup, as root, from a checkout of this repo. OpenSSH must already be
the system sshd (it is). `dropbearkey` must be installed because the
phone client cannot read an OpenSSH private key:

```sh
apt-get install dropbear-bin
sudo ./scripts/setup-phonetunnel-account.sh
```

Check the plan first:

```sh
sudo ./scripts/setup-phonetunnel-account.sh --dry-run
```

Dry-run writes nothing and does not reload sshd.

The script:

1. Creates unix user `phonetunnel` with shell `/bin/sh` and a shadow
   password of `*` (not `!`). The shell has to be `/bin/sh` so OpenSSH
   can run the hold script; `nologin` ignores `-c` and never runs it.
   A `!` lock makes OpenSSH refuse the public key even when passwords
   are off. A failed run deletes a user it just created.
2. Generates two keys under `/root/phonetunnel-install/` (mode 700):
   - `id_ed25519` from `dropbearkey`. This is the phone's tunnel identity.
     The matching public line goes in `phonetunnel`'s `authorized_keys`
     with `restrict`, `port-forwarding`, `permitlisten` for
     `127.0.0.1:2222` and `127.0.0.1:5556` only, and `permitopen="192.0.2.1:9"`
     (TEST-NET, so a local `-L` forward cannot reach a real host).
   - `phone_shell_ed25519` from `ssh-keygen`. Cup's OpenSSH client uses
     this to log into the phone. The public line is written for Dropbear
     with `no-port-forwarding,no-agent-forwarding,no-X11-forwarding`.
     A PTY is allowed, so `ssh -t` is an interactive rescue shell.
     A command without `-t` still works.
3. Writes `known_hosts` in Dropbear's form (`hostname algo base64`, no
   brackets, no hash) for every host key in `/etc/ssh`.
4. Writes `config.env` and `authorized_keys` for the phone.
5. Installs `/usr/local/sbin/phonetunnel-hold` and inserts a `Match User
   phonetunnel` block **before any other Match** in `/etc/ssh/sshd_config`.
   OpenSSH keeps the first value it saw for each keyword, so a block
   appended at the end loses to an earlier `Match`. The account shell is
   `/bin/sh`, not `nologin`: sshd runs `$SHELL -c` the ForceCommand, and
   `nologin` ignores `-c` and never runs the hold script. The Match sets
   `AllowTcpForwarding remote`, `AllowStreamLocalForwarding no`,
   `PermitOpen none`, `PermitTTY no`, and `PermitListen` for the two
   localhost ports. `restrict` does not turn off unix-socket forwards;
   `AllowStreamLocalForwarding no` does.
6. Runs `sshd -t`, then `sshd -T -C user=phonetunnel,host=127.0.0.1,addr=127.0.0.1`
   on a copy of the config. If the effective settings are any looser (an
   earlier Match inside `sshd_config.d` is the usual cause), the live
   file is left unchanged and sshd is not reloaded. Only a clean result
   creates the account, installs the hold script and keys, writes the
   Match, and reloads ssh. If one of those later steps fails, that run
   is rolled back.

`ssh -N` (what the phone uses) does not run ForceCommand. A client that
asks for a command runs the hold script, which prints
`phonetunnel: command refused` and exits 1. The requested command is
not executed. Do not point the account shell at `nologin` or
`/bin/false`; neither one runs the hold script.

Re-running the script keeps existing key files and replaces only this
account's authorized_keys line and the marked Match block.

Nothing secret is committed. After the phone has `id_ed25519` and
`phonetunnel-status` shows `tunnel=up` for a while, delete the phone
private key from cup:

```sh
shred -u /root/phonetunnel-install/id_ed25519
```

Keep `phone_shell_ed25519` on cup.

## Phone install

Build the zip on any machine with the repo (the arm64 binary is already
in `magisk-phonetunnel/bin/`):

```sh
./magisk-phonetunnel/build-module.sh
```

With a working adb session, and the line idle:

```sh
adb push phonetunnel-magisk.zip /data/local/tmp/phonetunnel-magisk.zip
adb shell su -c 'mkdir -p /data/adb/phonetunnel/home/.ssh'
adb shell su -c 'chmod 700 /data/adb/phonetunnel /data/adb/phonetunnel/home /data/adb/phonetunnel/home/.ssh'
```

Copy the bundle from cup to the phone (scp or `adb push`). Place:

| Bundle file | Phone path | Mode |
|---|---|---|
| `id_ed25519` | `/data/adb/phonetunnel/id_ed25519` | 600 |
| `known_hosts` | `/data/adb/phonetunnel/home/.ssh/known_hosts` | 644 |
| `authorized_keys` | `/data/adb/phonetunnel/authorized_keys` | 600 |
| `config.env` | `/data/adb/phonetunnel/config.env` | 600 |

`authorized_keys` in the bundle is the **phone** sshd file (the shell
key), not cup's file.

Before reboot, append cup's adb public key to
`/data/misc/adb/adb_keys` if you want `adb connect` from cup. A headless
phone cannot click the RSA prompt. The module does not write that file
and it does not start adbd.

Stock `adbd` has no localhost bind. Setting its TCP port makes it listen
on every interface, and the persisted property would still be set after
this module is removed. `start adbd` also does not restart an adbd that
is already running, so it cannot change the bind of the session you are
using. The module therefore never sets `service.adb.tcp.port` or
`persist.adb.tcp.port` and never starts or stops adbd. The reverse
forward still points at `127.0.0.1:5555`. If something is already
listening there (the adb you used to install), cup can use it. If
nothing is listening, `phonetunnel-status` says `phone_adb=down` and
`phone_adb_managed=untouched`. Bringing adb up is an operator action,
not something the module does.

```sh
adb shell su -c 'magisk --install-module /data/local/tmp/phonetunnel-magisk.zip'
adb reboot
```

Reboot only while the line is idle. After boot:

```sh
adb shell su -c phonetunnel-status
```

`tunnel=up` means the SSH session is up **and** the phone sshd is
listening. A live `dbclient` with a dead sshd is `tunnel=down` and
`tunnel_session=up`. `host_fingerprint` is the phone Dropbear host key
(`SHA256:...`). From cup:

```sh
ssh -i /root/phonetunnel-install/phone_shell_ed25519 \
    -o IdentitiesOnly=yes \
    -p 2222 root@127.0.0.1
adb connect 127.0.0.1:5556
```

Use `127.0.0.1`, not `localhost`. `PermitListen` matches the string the
client sends. The first shell login should check the Dropbear host
fingerprint against `phonetunnel-status` (or the phone log line from
`dropbearkey`) before you accept it. Cup's OpenSSH stores that in
`~/.ssh/known_hosts` for `[127.0.0.1]:2222`.

The phone client pins cup's host keys from the `known_hosts` file the
setup script wrote. `StrictHostKeyChecking=yes` refuses a changed key.

## Rollback

```sh
adb shell su -c 'touch /data/adb/modules/gsm2computer-phonetunnel/remove'
adb reboot
```

Magisk runs `uninstall.sh` on that boot. It stops this module's
processes only. It does not change adb properties, and it does not
stop the bridge. `gsm2computer-bridge` stays. Do not run
`magisk --remove-modules`.

Disable without uninstalling:

```sh
adb shell su -c 'touch /data/adb/modules/gsm2computer-phonetunnel/disable'
adb reboot
```

`/data/adb/phonetunnel` (keys, config, logs) is not inside the module
and survives removal. Delete it when you want the keys gone.
`/dev/phonetunnel-keys` is tmpfs and disappears on reboot; the
supervisor recreates it.

On cup, to undo the account, remove the marked Match block from
`/etc/ssh/sshd_config`, reload ssh, and `userdel phonetunnel`. A backup
of the config is next to it as `sshd_config.bak.phonetunnel`.

## Behaviour that matters

- One tunnel process. A second supervisor exits if the pid file is live.
- Keepalive is 45s. Dropbear treats three missed replies as a dead
  session, so a cup reboot or a vanished path is noticed in about two
  minutes, then the backoff loop dials again. That is a few hundred
  bytes a day, not a poll of the public internet beyond the one SSH
  session.
- The supervisor is `renice`d to 10.
- The watchdog only force-stops `com.tailscale.ipn`. Any other
  `TS_PACKAGE` is rejected, so a bad config cannot aim it at
  `com.gsm2computer.bridge`. It restarts only when `dumpsys
  telephony.registry` contains `mCallState=0` and no active precise or
  legacy call state (`mPreciseCallState=FG_ACTIVE` counts as a call).
  Empty output, a dumpsys error, or an unrecognized dump is not idle.
  `ip addr show tailscale0` must succeed and show the interface with no
  IPv4 before a miss counts. An `ip` error is not a down interface.
  `TS_CHECK_SEC` below 15 is raised to 15. `TS_RESTART_MIN_SEC` below
  60 is raised to 60. The loop never sleeps 0.
- Phone sshd key directory: Dropbear rejects `authorized_keys` if any
  parent directory is group- or world-writable. `/data` on Android
  usually is, so on a phone the keys are copied to
  `/dev/phonetunnel-keys`.
- Logs: `/data/adb/phonetunnel/phonetunnel.log`, rotated once past
  256 KiB. `service.log` is the boot redirect. `phonetunnel-ctl stop`
  stops the supervisor until the next boot or `phonetunnel-ctl start`.

## What a Linux stand-in does not prove

The check in `.cursor/skills/verify-phonetunnel/` runs on a Linux VM.
These steps are not what the rooted Moto or the real cup sshd do, and
they still need a look on the device while the line is idle:

- Cup in the stand-in is a private sshd on `127.0.0.1` at a high port.
  It does not edit `/etc/ssh/sshd_config` and it is not `62.83.35.197`.
- The stand-in copies `authorized_keys` into a local `phonetunnel` home
  because OpenSSH StrictModes rejects a key file under `/tmp`.
- The supervisor is started with `sh`, not by Magisk at boot. There is
  no module install and no `remove` file reboot.
- The arm64 binary runs under `qemu-aarch64-static`, not on the Moto
  CPU and not under Android SELinux.
- The shell through the tunnel is the harness user and `/bin/sh`, not
  `root` and `/system/bin/sh`.
- Adb is a Python banner, not `adbd`, and there is no
  `/data/misc/adb/adb_keys` prompt.
- Tailscale is fake `ip` / `am` / `dumpsys` programs, not
  `com.tailscale.ipn`.
- The network cut is an iptables DROP on loopback, not Wi-Fi versus
  mobile data and not carrier NAT.
- The cup reboot is a kill of that private sshd.
- The key directory is `~/.phonetunnel-keys` because `getprop` is
  absent. The phone uses `/dev/phonetunnel-keys`.
- Call-idle is a text file. The bridge app is not running, and battery
  or Doze was not measured.
- Stand-in keepalive is 2s and backoff caps at 4s. Production is 45s
  and 2..60s.
