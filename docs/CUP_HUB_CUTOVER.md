# Cup hub cutover

Switch the running hub from `~/gsm2computer-hub` to this repo's `hub/` with no change in what answers a call. Do this only while `/health` says `"busy": false`. Do not deploy from a cloud agent. Do not merge this as the deploy.

The live tree on cup is a separate git repo with no remote. Its tip on 2026-11-09 was build 338 (G2 binary uplink, drop-oldest downlink, `hub_call_watch`). This repo's `hub/` is that tree plus the public entrance, the device token, and `call.path`. Grok Bot stays the answering mode.

## Before

```bash
curl -sS http://100.113.222.47:8787/health | jq '{busy, auto_mode, entrances}'
systemctl --user status gsm2computer-hub.service
systemctl --user cat gsm2computer-hub.service
```

Stop if `busy` is true, if a call is up, or if `auto_mode` is not `grokbot`. The drop-in `~/.config/systemd/user/gsm2computer-hub.service.d/grokbot.conf` is what sets `GSM2COMPUTER_AUTO_MODE=grokbot`. The unit file itself still says `openclaw`. Leave that drop-in in place.

## What not to copy over the running unit

The unit file in this repo sets `GSM2COMPUTER_HUB_HOST=100.113.222.47`. The Oct 2026 live unit still said `100.101.181.110`. That address is not cup. Do not install `hub/gsm2computer-hub.service` over the running unit. Read the installed `Environment=` lines and keep them.

Public-entrance variables stay commented until the firewall include and Caddy are confirmed. Until `GSM2COMPUTER_PUBLIC_ENABLE=1`, the hub keeps answering on the Tailscale socket only.

## Code switch

Keep the old directory. Instant rollback is pointing the unit back at it.

```bash
test "$(curl -sS http://127.0.0.1:8787/health | jq -r .busy)" = false
cp -a ~/gsm2computer-hub ~/gsm2computer-hub.rollback-$(date -u +%Y%m%dT%H%M%SZ)
# state, the portal sqlite, the venv, and the device-token file stay with the old dir
rsync -a --delete \
  --exclude state/ --exclude var/ --exclude workspace/ --exclude backups/ \
  --exclude '.venv/' --exclude 'portal.sqlite*' --exclude device-tokens.json \
  --exclude __pycache__/ \
  ~/gsm2computer/hub/ ~/gsm2computer-hub/
systemctl --user restart gsm2computer-hub.service
curl -sS http://127.0.0.1:8787/health | jq '{busy, auto_mode, entrances, grokbot}'
```

`auto_mode` stays `grokbot` because the drop-in is still installed. `entrances` lists `tailscale` until the public socket is enabled. A test call from the phone should still be answered by Suzie.

## Rollback

```bash
test "$(curl -sS http://127.0.0.1:8787/health | jq -r .busy)" = false
systemctl --user stop gsm2computer-hub.service
rm -rf ~/gsm2computer-hub
mv ~/gsm2computer-hub.rollback-<stamp> ~/gsm2computer-hub
systemctl --user start gsm2computer-hub.service
```

The drop-ins are outside that directory, so Grok Bot, the e2e timer, and the call-end watch do not move with the code.

## Units and env that stay

| Piece | Where | Keep |
|---|---|---|
| Hub | `~/.config/systemd/user/gsm2computer-hub.service` | Installed `HOST`, `PORT`, `TALK`. Do not overwrite from the repo unit. |
| Grok Bot | `gsm2computer-hub.service.d/grokbot.conf` from `hub/ops/grokbot/grokbot.conf` | `AUTO_MODE=grokbot`, `BOT=suzie` |
| Watchdog | `gsm2computer-hub-watchdog.service` + `.timer` | Unchanged |
| Call-end watch | `gsm2computer-call-end-watch.service` | ADB `:5555`, hub `:8787` |
| E2E | `gsm2computer-e2e.service` + `.timer` | Every 30 minutes, grokbot profile |
| Portal Tailscale | `tailscale-portal.service` | `--port=41642`. The host filter must keep UDP 41642. |
| Device token | `device-tokens.json` next to `hub.py` | Not in git. The phone and the hub share it. |

`GSM2COMPUTER_PUBLIC_ENABLE`, `GSM2COMPUTER_PUBLIC_HOST`, `GSM2COMPUTER_PUBLIC_PORT`, and `GSM2COMPUTER_PUBLIC_TLS_PORT` turn on the second socket. Leave them unset for this cutover. The firewall drop-in is a separate step in [CUP_PUBLIC_ENTRANCE.md](CUP_PUBLIC_ENTRANCE.md).
