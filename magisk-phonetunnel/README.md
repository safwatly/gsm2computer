# gsm2computer-phonetunnel

Magisk module that keeps an outbound SSH reverse tunnel from the phone
to cup, plus a localhost sshd and a Tailscale watchdog. It does not
change the `gsm2computer-bridge` module or the bridge app.

Full cup and phone procedure: [docs/PHONETUNNEL.md](../docs/PHONETUNNEL.md).

## What it does after boot

`service.sh` starts `bin/supervisor.sh`, which:

- runs Dropbear on `127.0.0.1:2222` (key-only, no forwarding, no passwords)
- if nothing is already listening on `127.0.0.1:5555`, starts adbd on that port; it does not restart an adbd that is already up
- keeps one `dbclient` session to cup (`62.83.35.197:22` by default) with two reverse forwards: cup `127.0.0.1:2222` to the phone sshd, and cup `127.0.0.1:5556` to phone adb
- reconnects with backoff (2s, then doubles, cap 60s) after a network change, a cup reboot, or a dead session; a session that lasted at least 60s resets the backoff
- runs `bin/watchdog.sh`, which restarts only `com.tailscale.ipn` when `tailscale0` has no IPv4 for 3 checks (about 3 minutes), at most once per 15 minutes, and never during a call

The phone identity must be a Dropbear private key. An OpenSSH private key is refused. The setup script on cup generates the right file.

## Build

```sh
./magisk-phonetunnel/build-module.sh
```

That writes `phonetunnel-magisk.zip` (gitignored). It contains no keys.
Rebuild the arm64 binary only when Dropbear changes:

```sh
./magisk-phonetunnel/build-binaries.sh
```

Provenance is in `BINARIES.md`.

## Install on the phone

Do this only while the line is idle. You need a working adb session
(today that is Tailscale). The module cannot be installed from this repo
alone; cup must be set up first so the key bundle exists. See the doc.

```sh
adb push phonetunnel-magisk.zip /data/local/tmp/phonetunnel-magisk.zip
adb shell su -c 'magisk --install-module /data/local/tmp/phonetunnel-magisk.zip'
```

Copy the four phone files from the cup bundle into `/data/adb/phonetunnel/`
before the reboot (paths are in the setup script output). Then reboot
while the line is still idle.

After boot:

```sh
adb shell su -c phonetunnel-status
```

## Logs and control

| | |
|---|---|
| Log | `/data/adb/phonetunnel/phonetunnel.log` (rotated at 256 KiB to `.1`) |
| Boot log | `/data/adb/phonetunnel/service.log` |
| Status | `phonetunnel-status` or `phonetunnel-ctl status` |
| Stop until next boot | `phonetunnel-ctl stop` |
| Start | `phonetunnel-ctl start` |

Keys and `config.env` live in `/data/adb/phonetunnel`, not in the module
directory, so removing the module does not delete them.

## Rollback

```sh
adb shell su -c 'touch /data/adb/modules/gsm2computer-phonetunnel/remove'
adb reboot
```

Reboot while the line is idle. That removes only this module on the next
boot. Do not run `magisk --remove-modules`; that also removes
`gsm2computer-bridge`.

To disable without deleting:

```sh
adb shell su -c 'touch /data/adb/modules/gsm2computer-phonetunnel/disable'
adb reboot
```

To drop the keys after the module is gone:

```sh
adb shell su -c 'rm -rf /data/adb/phonetunnel'
```
