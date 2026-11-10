# Grok Bot answering mode

- `GSM2COMPUTER_AUTO_MODE=grokbot` answers a GSM call with Grok Bot (Suzie) through patchbay. OpenClaw stays in the tree and is selected with `openclaw`.
- The hub writes caller audio to `patchbay_mic` and reads `grok_bus`. It does not join GSM into the patchbay room.
- `/health` carries `auto_mode` and `grokbot` (`live`, `switching`, `last_switch`, `last_off`) on both the public and Tailscale sockets.
- `session.updated` uses model `gsm2computer-hub-grokbot` and `talk=grokbot`, and `session.entrance` is still the socket that accepted the call.
- Build 338 keeps a drop-oldest downlink queue. Phone builds send G2 binary uplink frames (`G2`, version 1, s16le mono). The public entrance accepts those frames when `X-Gsm-Device-Token` is valid.
- `call_slot.disruptive_heal_blocked` treats grokbot `live` or `switching` as busy.
- After `patchbay off`, the hub writes `transcript.json` beside that call's recordings (`caller` / `assistant` turns) for the voice session that started during the call. The desktop renderer `file://…/resources/app.asar/dist/renderer/index.html` counts as that page on loopback CDP. Its chat is `[role=article]` rows in the Conversation transcript log (speaker text `You` or the bot name), not grok.com `/c/` links. `call_ended` may say `pending`. The process that posted `call_ended` (`call_end_watch.py`, or `hub_call_watch.py` when adb did not fire) then posts `call_transcript` with the same webhook, waiting on that call's tap directory (known when the call starts, not `last_call_tap` after the mix). The hub unit does not. Restarting the watcher during the wait drops the follow-up thread; the file stays on disk. A new call aborts the read before `patchbay switch`. A missed read sets `transcript.status` and does not fail the call.
- Every mode also writes `phone-downlink-8k-mono.wav` in the call directory (audio handed to the phone). How to read both files is in [hub/GROKBOT.md](../../../../hub/GROKBOT.md).

## How an operator does it

The running unit on cup keeps the drop-in `~/.config/systemd/user/gsm2computer-hub.service.d/grokbot.conf` (`ops/grokbot/grokbot.conf`). Do not remove it when switching the code tree. Rollback to OpenClaw is deleting that drop-in and restarting the user unit while `/health` shows `busy: false`. See [hub/GROKBOT.md](../../../../hub/GROKBOT.md) and [docs/CUP_HUB_CUTOVER.md](../../../../docs/CUP_HUB_CUTOVER.md).

## How this skill drives it

```bash
python3 -m unittest hub.tests.test_grokbot_mode hub.tests.test_reconcile_interactions
.cursor/skills/verify-gsm2computer/scripts/drive-bridge-paths.sh
```

`test_grokbot_mode` uses a fake patchbay. `test_reconcile_interactions` places a stubbed call on the public socket in grokbot mode and checks `session.entrance`, `POST /calls` path, and a G2 frame. The drive script runs that test and writes `bridge-paths.json`. It does not start patchbay or place a GSM call.

## Gotchas

- A cloud VM has no patchbay and no Grok Bot app. A passing test does not prove Suzie answered.
- Leaving `AUTO_MODE=grokbot` unset keeps the OpenClaw path. The verify launcher sets `AUTO_MODE=loopback` and `OPENCLAW_TALK=off` so the entrance drive does not need Chromium.
