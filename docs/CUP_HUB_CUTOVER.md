# Cup hub cutover

Switch the running hub from `~/gsm2computer-hub` to this repo's `hub/` with no change in what answers a call. Do this only while the hub is idle and the phone is idle. Do not deploy from a cloud agent. Do not merge this as the deploy.

The live tree on cup is a separate git repo with no remote. Its tip on 2026-11-09 was build 338 (G2 binary uplink, drop-oldest downlink, `hub_call_watch`). This repo's `hub/` is that tree plus the public entrance, the device token, and `call.path`. Grok Bot stays the answering mode.

```bash
hub/ops/cutover.sh apply
hub/ops/cutover.sh rollback
```

`apply` and `rollback` both refuse to continue unless the same gate passes. The gate reads `GSM2COMPUTER_HUB_HOST` and `GSM2COMPUTER_HUB_PORT` from the installed `gsm2computer-hub.service` (then from the process environment if the unit does not set them) and GETs `http://<host>:<port>/health`. `busy` must be JSON `false`. When `GSM2COMPUTER_CALL_END_ADB` is set on `gsm2computer-call-end-watch.service`, the same gate also runs `adb shell dumpsys telephony.registry` and requires `mCallState=0`. Anything else, including a hub that does not answer, exits 1 before `cp`, `rsync`, `stop`, or `restart`.

`apply` then copies `~/gsm2computer-hub` to `~/gsm2computer-hub.rollback-<stamp>`, rsyncs this repo's `hub/` over the live dir (keeping `state/`, `var/`, `workspace/`, `backups/`, `.venv/`, `portal.sqlite*`, and `device-tokens.json`), and restarts the user unit. `rollback` stops the unit, moves that backup back into place, and starts the unit.

## What not to copy over the running unit

The unit file in this repo sets `GSM2COMPUTER_HUB_HOST=100.113.222.47`. The Oct 2026 live unit still said `100.101.181.110`. That address is not cup. Do not install `hub/gsm2computer-hub.service` over the running unit. Read the installed `Environment=` lines and keep them.

Public-entrance variables stay commented until the firewall include and Caddy are confirmed. Until `GSM2COMPUTER_PUBLIC_ENABLE=1`, the hub keeps answering on the Tailscale socket only.

## Required cup env

These values stay in cup drop-ins. They are not in git. `apply` reads the installed units and exits 1 if any value is empty.

| Variable | Installed unit |
|---|---|
| `GSM2COMPUTER_HUB_HOST` | `gsm2computer-hub.service` |
| `GSM2COMPUTER_HUB_PORT` | `gsm2computer-hub.service` |
| `MOTO_TS_IP` | `gsm2computer-hub.service` |
| `GSM2COMPUTER_CALL_END_ADB` | `gsm2computer-call-end-watch.service` |
| `GSM2COMPUTER_HUB_HTTP` | `gsm2computer-call-end-watch.service` |
| `GSM2COMPUTER_E2E_HUB` | `gsm2computer-openclaw-e2e.service` |
| `CUP_WATCHDOG_HUB` | `cup-watchdog.service` |
| `CUP_WATCHDOG_PIXEL_IP` | `cup-watchdog.service` |

`GSM2COMPUTER_CALL_END_ADB` is `host:5555` for the phone's adb. `GSM2COMPUTER_HUB_HTTP`, `GSM2COMPUTER_E2E_HUB`, and `CUP_WATCHDOG_HUB` are `http://<hub-bind>:<port>`. `MOTO_TS_IP` is the phone peer `hub_call_watch.py` looks up in `tailscale status`. `CUP_WATCHDOG_PIXEL_IP` is the address `cup_watchdog.py` pings. `GSM2COMPUTER_HUB_URL` is the optional e2e fallback when the primary hub URL does not answer; leave it unset to skip that fallback.

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

| Piece | Where | Keep |
|---|---|---|
| Hub | `~/.config/systemd/user/gsm2computer-hub.service` | Installed `HOST`, `PORT`, `TALK`, and `MOTO_TS_IP`. Do not overwrite from the repo unit. |
| Grok Bot | `gsm2computer-hub.service.d/grokbot.conf` from `hub/ops/grokbot/grokbot.conf` | `AUTO_MODE=grokbot`, `BOT=suzie` |
| Watchdog | `cup-watchdog.service` + `.timer` | `CUP_WATCHDOG_HUB`, `CUP_WATCHDOG_PIXEL_IP` |
| Call-end watch | `gsm2computer-call-end-watch.service` | `GSM2COMPUTER_CALL_END_ADB`, `GSM2COMPUTER_HUB_HTTP` |
| E2E | `gsm2computer-openclaw-e2e.service` + `.timer` | `GSM2COMPUTER_E2E_HUB`. Every 30 minutes, grokbot profile |
| Portal Tailscale | `tailscale-portal.service` | `--port=41642`. The host filter must keep UDP 41642. |
| Device token | `device-tokens.json` next to `hub.py` | Not in git. The phone and the hub share it. |

The drop-ins are outside the code directory, so Grok Bot, the e2e timer, and the call-end watch do not move with the tree. `auto_mode` stays `grokbot` because the drop-in is still installed. `entrances` lists `tailscale` until the public socket is enabled.

`GSM2COMPUTER_PUBLIC_ENABLE`, `GSM2COMPUTER_PUBLIC_HOST`, `GSM2COMPUTER_PUBLIC_PORT`, and `GSM2COMPUTER_PUBLIC_TLS_PORT` turn on the second socket. Leave them unset for this cutover. The firewall drop-in is a separate step in [CUP_PUBLIC_ENTRANCE.md](CUP_PUBLIC_ENTRANCE.md).
