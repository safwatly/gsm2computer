# Cup hub cutover

Switch the running hub from `~/gsm2computer-hub` to this repo's `hub/` with no change in what answers a call. Do this only while the hub is idle and the phone is idle. Do not deploy from a cloud agent. Do not merge this as the deploy.

The live tree on cup is a separate git repo with no remote. Its tip on 2026-11-09 was build 338 (G2 binary uplink, drop-oldest downlink, `hub_call_watch`). This repo's `hub/` is that tree plus the public entrance, the device token, and `call.path`. Grok Bot stays the answering mode.

```bash
hub/ops/cutover.sh apply
hub/ops/cutover.sh rollback
```

`apply` and `rollback` both refuse to continue unless the same gate passes. The gate reads `GSM2COMPUTER_HUB_HOST` and `GSM2COMPUTER_HUB_PORT` from the installed `gsm2computer-hub.service` (then from the process environment if the unit does not set them) and GETs `http://<host>:<port>/health`. The live body has no top-level `busy`. The gate reads `call.busy`, and uses a top-level `busy` only when `call.busy` is absent. That value must be JSON `false`. A missing field, `true`, or any other value exits 1. When `GSM2COMPUTER_CALL_END_ADB` is set on `gsm2computer-hub-call-watch.service` (or, if that is empty, on `gsm2computer-call-end-watch.service`), the same gate also runs `adb shell dumpsys telephony.registry` and requires `mCallState=0`. Anything else, including a hub that does not answer, exits 1 before `cp`, `rsync` (including the dry run), `stop`, or `restart`.

`apply` then copies `~/gsm2computer-hub` to `~/gsm2computer-hub.rollback-<stamp>`, prints the `rsync --delete` dry-run list, rsyncs this repo's `hub/` over the live dir, and restarts `gsm2computer-hub.service`, `gsm2computer-hub-call-watch.service`, and `gsm2computer-call-end-watch.service`. `rollback` stops those three, moves that backup back into place, and starts them. The gate runs before either restart. `gsm2computer-call-end-watch.service` runs `call_end_watch.py` with `WorkingDirectory=~/gsm2computer-hub`, so it has to be down before the tree moves and up again on the new tree.

## What not to copy over the running unit

The unit file in this repo sets `GSM2COMPUTER_HUB_HOST=100.113.222.47`. The Oct 2026 live unit still said `100.101.181.110`. That address is not cup. Do not install `hub/gsm2computer-hub.service` over the running unit. Read the installed `Environment=` lines and keep them.

Public-entrance variables stay commented until the firewall include and Caddy are confirmed. Until `GSM2COMPUTER_PUBLIC_ENABLE=1`, the hub keeps answering on the Tailscale socket only.

## Required cup env

These values stay in cup drop-ins. They are not in git. `apply` reads the installed units and exits 1 if any value is empty.

| Variable | Installed unit |
|---|---|
| `GSM2COMPUTER_HUB_HOST` | `gsm2computer-hub.service` |
| `GSM2COMPUTER_HUB_PORT` | `gsm2computer-hub.service` |
| `MOTO_TS_IP` or `GSM2COMPUTER_CALL_END_ADB` | `gsm2computer-hub-call-watch.service` |
| `GSM2COMPUTER_CALL_END_ADB` | `gsm2computer-call-end-watch.service` |
| `GSM2COMPUTER_HUB_HTTP` | `gsm2computer-call-end-watch.service` and `gsm2computer-hub-call-watch.service` |
| `GSM2COMPUTER_E2E_HUB` | `gsm2computer-openclaw-e2e.service` |
| `CUP_WATCHDOG_HUB` | `cup-watchdog.service` |
| `CUP_WATCHDOG_PIXEL_IP` | `cup-watchdog.service` |

`GSM2COMPUTER_CALL_END_ADB` is `host:5555` for the phone's adb. `hub_call_watch.py` reads the phone address from its own process: `MOTO_TS_IP` if that is set, otherwise the host part of `GSM2COMPUTER_CALL_END_ADB`. On cup that process is `gsm2computer-hub-call-watch.service`. The address belongs only in `gsm2computer-hub-call-watch.service.d/cup-private.conf`. The unit that was already installed on cup has `GSM2COMPUTER_CALL_END_ADB` (and maybe `MOTO_TS_IP`) inline in the unit file. Move those lines into `cup-private.conf` and delete them from the unit file before installing `hub/gsm2computer-hub-call-watch.service`. That file does not contain the phone address. `systemctl --user daemon-reload` after the move. `apply` reads the merged environment, so the drop-in satisfies the check.

`GSM2COMPUTER_HUB_HTTP`, `GSM2COMPUTER_E2E_HUB`, and `CUP_WATCHDOG_HUB` are `http://<hub-bind>:<port>`. `CUP_WATCHDOG_PIXEL_IP` is the address `cup_watchdog.py` pings. `GSM2COMPUTER_HUB_URL` is the optional e2e fallback when the primary hub URL does not answer; leave it unset to skip that fallback.

Example drop-in, filled in on cup and not committed:

```ini
[Service]
Environment=MOTO_TS_IP=
Environment=GSM2COMPUTER_CALL_END_ADB=
Environment=GSM2COMPUTER_HUB_HTTP=
Environment=GSM2COMPUTER_E2E_HUB=
Environment=CUP_WATCHDOG_HUB=
Environment=CUP_WATCHDOG_PIXEL_IP=
```

Put each key on the unit that owns it (`~/.config/systemd/user/<unit>.d/cup-private.conf`), then `systemctl --user daemon-reload`.

## Units that stay

| Piece | Where on cup | Keep |
|---|---|---|
| Hub | `~/.config/systemd/user/gsm2computer-hub.service` | Installed `HOST`, `PORT`, and `TALK`. Do not overwrite from the repo unit. |
| Grok Bot drop-in | `gsm2computer-hub.service.d/grokbot.conf` (source `hub/ops/grokbot/grokbot.conf`) | `AUTO_MODE=grokbot`, `BOT=suzie` |
| Hub call watcher | `gsm2computer-hub-call-watch.service` (source `hub/gsm2computer-hub-call-watch.service`) | Phone address only in `cup-private.conf`, moved off the old unit file. `WorkingDirectory=%h/gsm2computer-hub`. Restarted with the hub on apply and rollback. |
| Watcher webhook drop-in | `gsm2computer-hub-call-watch.service.d/alert-webhook.conf` | `GSM2COMPUTER_ALERT_WEBHOOK_URL` and `GSM2COMPUTER_ALERT_WEBHOOK_KEY`. Not in git. The adb call-end unit needs the same drop-in when it is the process that posts `call_ended` (see below). |
| Watcher phone drop-in | `gsm2computer-hub-call-watch.service.d/cup-private.conf` | `GSM2COMPUTER_CALL_END_ADB` and, if used, `MOTO_TS_IP`. Not in git. |
| Watchdog | `cup-watchdog.service` + `.timer` | `CUP_WATCHDOG_HUB`, `CUP_WATCHDOG_PIXEL_IP` |
| Adb call-end watch | `gsm2computer-call-end-watch.service` (runs `call_end_watch.py` from `~/gsm2computer-hub`) | `GSM2COMPUTER_CALL_END_ADB`, `GSM2COMPUTER_HUB_HTTP`. Also `GSM2COMPUTER_ALERT_WEBHOOK_URL` and `KEY` when this unit posts `call_ended`. Restarted with the hub on apply and rollback. |
| E2E | `gsm2computer-openclaw-e2e.service` + `.timer` | `GSM2COMPUTER_E2E_HUB`. Every 30 minutes, grokbot profile |
| Portal Tailscale | `tailscale-portal.service` | `--port=41642`. The host filter must keep UDP 41642. |
| Device token | `device-tokens.json` next to `hub.py` | Not in git. The phone and the hub share it. |

The drop-ins live under `~/.config/systemd/user/` and are outside the code directory, so Grok Bot, the alert webhook, the e2e timer, and both call watchers do not move with the tree. `auto_mode` stays `grokbot` because `grokbot.conf` is still installed. `entrances` lists `tailscale` until the public socket is enabled.

`gsm2computer-hub-call-watch.service` is the process that runs `hub_call_watch.py`. After the move, `cup-private.conf` sets `GSM2COMPUTER_CALL_END_ADB` to the phone (`host:5555`). `MOTO_TS_IP` in that same drop-in is the other accepted form. The hub unit does not carry the phone address, and neither does the call-watch unit file in this repo. `alert-webhook.conf` only supplies the webhook; an unset URL makes alerts a no-op.

`call_transcript` is posted by the process that posted `call_ended`, with that process's URL, key, and the shared spool (`~/.local/state/gsm2computer/alert-webhook/outbox.sqlite3`). The hub unit does not post it and must not grow its own webhook drop-in. On a normal hangup the poster is `gsm2computer-call-end-watch.service`. It waits for `transcript.json` (about 25s) without delaying `call_ended`. That wait is a daemon thread: restarting the unit during it drops `call_transcript` (the file remains for the portal and is not retried). When adb did not fire, `gsm2computer-hub-call-watch.service` posts both events and already has this drop-in. If `call_ended` alerts already arrive from the call-end unit, no cup change is required. If that unit's environment has no webhook URL, copy `alert-webhook.conf` onto `gsm2computer-call-end-watch.service.d/` (the same URL and key, not a second secret) and reload that unit.

## What rsync --delete removes

`apply` prints `cutover: --delete would remove:` and the paths, then runs the same rsync for real. A path is removed when it is in `~/gsm2computer-hub` and not in this repo's `hub/`, unless it is protected.

Removed, and listed first:

- Source files and scripts that this repo no longer has.
- Old hashed files under `portal/dist` that the new build replaced.
- Anything else in the live tree that is not protected. A `*.bak` file is protected, so a backup sitting next to a source file stays.

Protected (left in place):

| Path | Why it stays |
|---|---|
| `.git/` | The live repo's own history. It has no remote. |
| `.cup-health*` | Watchdog state `cup_watchdog.py` reads (`.cup-health-checked-calls`, `.cup-health-reply-audio-state.json`). |
| `.health-watch*` | Watchdog notes under `.health-watch/`. |
| `node_modules/` | `portal/node_modules`. Not in git. |
| `.venv/` | The hub virtualenv the units execute. |
| `device-tokens.json` | Shared with the phone. Not in git. |
| `portal.sqlite*` | Portal messages, calls, and outbox, including `-wal` and `-shm`. `portal_store.py` writes this next to `hub.py`. |
| `state/`, `var/`, `workspace/`, `backups/` | Runtime directories already kept off the code sync. |
| `__pycache__/` | Bytecode. Rebuilt on import. |
| `*.bak` and `*.bak*` | Local backups of source files. |

These are outside `~/gsm2computer-hub`, so this rsync does not touch them: call recordings in `~/gsm2computer-calls` (`call_tap.RECORD_DIR`) and `~/gsm2computer-e2e-calls`, the link-gap log and call-end state in `~/gsm2computer-call-end-watch`, e2e state in `~/gsm2computer-e2e`, and the alert spool in `~/.local/state/gsm2computer/`.

`GSM2COMPUTER_PUBLIC_ENABLE`, `GSM2COMPUTER_PUBLIC_HOST`, `GSM2COMPUTER_PUBLIC_PORT`, and `GSM2COMPUTER_PUBLIC_TLS_PORT` turn on the second socket. Leave them unset for this cutover. The firewall drop-in is a separate step in [CUP_PUBLIC_ENTRANCE.md](CUP_PUBLIC_ENTRANCE.md).
