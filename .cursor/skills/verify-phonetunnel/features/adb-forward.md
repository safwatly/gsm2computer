# Adb through the tunnel

## Sub-features

- Cup `127.0.0.1:5556` is forwarded to the phone's adb port `127.0.0.1:5555`.
- The module does not start adbd and does not set `service.adb.tcp.port` or `persist.adb.tcp.port`. Stock adbd would listen on every interface, and a persisted port would survive module removal. `start adbd` would also not restart an already-running daemon.
- If the port is already open, the forward reaches it. If it is closed, status says `phone_adb=down` and `phone_adb_managed=untouched`.

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
- `drive-policy` greps the module scripts and fails if they set adb
  properties or run `start adbd`. That is not a test of real adbd.
- The real phone still needs the cup adb key in `/data/misc/adb/adb_keys`
  before the first `adb connect`. This skill never writes that file.
- Production maps cup 5556 to phone 5555. The stand-in picks free ports
  and records them in the run directory's `ports` file.
