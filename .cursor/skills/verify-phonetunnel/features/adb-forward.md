# Adb through the tunnel

## Sub-features

- Cup `127.0.0.1:5556` is forwarded to the phone's adb port `127.0.0.1:5555`.
- An adbd that is already listening is left running, so an existing Tailscale adb session is not dropped at install.
- If the port is closed, the supervisor sets `service.adb.tcp.port` and `persist.adb.tcp.port` and runs `start adbd`.

## How to get to it (user POV)

Before the reboot that installs the module, append cup's adb public key
to `/data/misc/adb/adb_keys`. Otherwise the headless phone shows an RSA
prompt nobody can tap.

From cup, once `tunnel=up`:

```sh
adb connect 127.0.0.1:5556
adb -s 127.0.0.1:5556 shell getprop ro.product.device
```

Expect the Moto g84 device name, not a connection refused.

## Driving it with the stand-in harness

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-adb
```

Connects to the stand-in adb remote port, sends `adb-probe\n`, and
requires the reply `adb-standin-ok`.

Evidence: `/opt/cursor/artifacts/verify-phonetunnel/adb-forward.txt`.

## Gotchas

- The stand-in listener is a Python banner, not `adbd`. It does not
  speak the adb protocol, so `adb connect` against the stand-in fails
  and that is expected. The drive proves the TCP forward only.
- The real phone still needs the cup adb key in `/data/misc/adb/adb_keys`
  before the first `adb connect`. This skill never writes that file.
- Production maps cup 5556 to phone 5555. The stand-in picks free ports
  and records them in the run directory's `ports` file.
