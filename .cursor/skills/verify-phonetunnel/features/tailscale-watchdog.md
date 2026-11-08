# Tailscale watchdog

## Sub-features

- If `tailscale0` has no IPv4, count a streak. Restart only at `TS_DOWN_STREAK` (3 in production, 2 in the drive).
- Restart is `am force-stop com.tailscale.ipn` and then `am start` of `com.tailscale.ipn/.IPNActivity`. No other package.
- A restart inside `TS_RESTART_MIN_SEC` (900s production, 100s in the drive) is skipped.
- `mCallState=1` or `2` clears the streak and does not restart.
- On a phone, a missing `dumpsys` does not restart (fail closed). The stand-in has no `getprop`, so a missing `dumpsys` is treated as idle so the fake can run.

## How to get to it (user POV)

Nothing to turn on. After boot the watchdog loops inside the supervisor.

If Tailscale's interface has no address for about three minutes and no
call is up, logcat tag `Phonetunnel` and
`/data/adb/phonetunnel/phonetunnel.log` contain
`watchdog: restarting com.tailscale.ipn`. `phonetunnel-status` shows
`watchdog_last_restart` as a timestamp. A second restart does not happen
for 15 minutes even if the interface stays down.

During a call the log says `call active or unknown; leaving Tailscale alone`.

## Driving it with the stand-in harness

```sh
.cursor/skills/verify-phonetunnel/scripts/harness.sh drive-watchdog
```

This does not use the live tunnel. It writes a private config and fake
`ip`, `am`, and `dumpsys` under the run directory, then runs
`watchdog.sh once` four times:

1. Interface down, streak 1 of 2. `am` is not called.
2. Interface down again. `am` log is `force-stop com.tailscale.ipn` and `start -n com.tailscale.ipn/com.tailscale.ipn.IPNActivity`.
3. Interface still down, last restart is now. `am` is not called again.
4. Streak is forced high, `mCallState=2`, last restart cleared. `am` is not called.
5. The same config with `TS_PACKAGE=com.gsm2computer.bridge`. `am` is not called, and the log says the package must be `com.tailscale.ipn`.

Evidence: `/opt/cursor/artifacts/verify-phonetunnel/watchdog.txt`.

## Gotchas

- The fake `am` records arguments and exits 0. It does not prove the
  activity name still exists in the installed Tailscale app. If
  `IPNActivity` moved, the phone log will show the `am start` failure
  and the `MAIN` intent fallback.
- `load_config` in `common.sh` accepts only `TS_PACKAGE=com.tailscale.ipn`
  and an activity under `com.tailscale.ipn/`. Any other package, including
  `com.gsm2computer.bridge`, makes the config invalid and the watchdog
  does not call `am`.
- The stand-in never has a real `tailscale0` or a real telephony
  registry. A GSM call on the Moto is the check that matters for "do
  not disturb the bridge".
