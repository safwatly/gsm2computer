# Grok Bot live voice on GSM calls (AUTO_MODE=grokbot)

Replaces OpenClaw Talk as the call answerer. OpenClaw code stays and is
selected with `GSM2COMPUTER_AUTO_MODE=openclaw`.

## Audio path (hub-owned, mix-minus by construction)

    caller WS uplink -> hub pw-cat -> patchbay_mic -> grok_mic -> Grok Bot app (Suzie)
    Grok Bot app -> grok_bus -> hub pw-record (sink capture) -> WS downlink -> caller

The patchbay room stays with GSM *unjoined*; the hub writes/reads the room
devices itself, so no pw-loopback is created per call. The bot only ever
hears the caller.

## Lifecycle

* Call start (after `session.updated`): `patchbay switch $GSM2COMPUTER_GROKBOT_BOT --json`
  runs beside the WS loop (caller hears patchbay's hold tone). Exit 4 retried once.
* Call end (hub releases the slot: clean hangup, watchdog abort, or relink grace
  expired, not a bare WS blip): `patchbay off --json` in the background, after any
  still-running switch. The next call waits for a pending off.
* `/health` carries `auto_mode` and `grokbot` {live, switching, last_switch, last_off}.
  `call_slot.disruptive_heal_blocked` treats grokbot live/switching as busy.

## Taps / review

`grok-mic-48k-stereo` (what Suzie hears, from call start) and
`grok-spk-48k-stereo` (Suzie, started only after the switch succeeds, so the
hold tone is not counted). Call dirs are named `<stamp>-grokbot`.
call_end_watch, cup_watchdog reply_audio and the post-call silent check read
the grok-* taps for these calls.

## Self-test

`openclaw_e2e.py` detects grokbot mode from `/health`:
* timer runs: `patchbay selftest` only (no live voice, no chat entries);
* `openclaw_e2e.py --live` or a hub live-request after a silent call: one
  synthetic call over `/e2e-test`; PASS needs grok-mic, grok-spk, downlink above
  0.02 peak and Suzie audio in the window after the prompt.

## Env

| var | default |
| --- | --- |
| GSM2COMPUTER_AUTO_MODE | openclaw (set `grokbot`) |
| GSM2COMPUTER_GROKBOT_BOT | suzie |
| GSM2COMPUTER_PATCHBAY_BIN | ~/bin/patchbay |
| GSM2COMPUTER_GROKBOT_UPLINK_GAIN | 1.0 |
| GSM2COMPUTER_GROKBOT_SWITCH_TIMEOUT_S | 60 |

## Deploy / rollback (only while /health idle)

Deploy: `ops/grokbot/grokbot.conf` -> `~/.config/systemd/user/gsm2computer-hub.service.d/`,
`systemctl --user daemon-reload && systemctl --user restart gsm2computer-hub`.

Rollback to OpenClaw:

    rm ~/.config/systemd/user/gsm2computer-hub.service.d/grokbot.conf && systemctl --user daemon-reload && systemctl --user restart gsm2computer-hub
