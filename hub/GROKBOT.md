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

Every call, not only Grok Bot, also writes `phone-downlink-8k-mono.wav`: the
mono PCM the hub handed to the phone, stored at 8 kHz. The pump queues those
frames and a side task writes the WAV, so a slow disk does not stall the
downlink. Existing stems and the room MP3s stay. The file is not deleted when
those rooms are mixed. Retention is still `GSM2COMPUTER_CALL_RECORD_KEEP`
directories (default 20). The queue drops frames if it gets about two seconds
behind.

```bash
ffprobe ~/gsm2computer-calls/<call-id>/phone-downlink-8k-mono.wav
```

Silence in that file means the hub did not encode audio toward the phone.
The older `gsm-downlink-8k-mono` stem is the bus-rate monitor and is removed
after `gsm.mp3` mixes successfully. `phone-downlink-8k-mono.wav` is the one
that remains for every mode, including loopback.

## Voice transcript

After `patchbay off` (never during the live session), the hub reads the Grok
page over a loopback CDP debugger URL. It accepts only a voice-session link
whose start time is during this call, or up to 30s after hangup. A link with
no start time is not a match, and neither is a session that started before the
call. The click is armed with a page token and does not run if that token was
cleared after the click expression was sent, which is how a new call aborts
an in-flight open. An unlabeled `/c/` chat is not opened. Zero matches are
`unmatched`. Two sessions that share the newest in-window start are
`ambiguous`. Turns that are not clearly the caller or the assistant are
dropped. The page host must be `grok.com`, `grok.x.ai`, or `x.com/i/grok` — a
host that merely contains the name is ignored — and `webSocketDebuggerUrl`
must be loopback.

The read gives up after about 12 seconds. A new call does not wait that out:
it aborts the reader (about 1 second) and then runs `patchbay switch`, so the
click cannot land on the next caller's session. A failure logs the reason,
records `transcript_status` (`no_cdp`, `off_failed`, `empty`, `unmatched`,
`ambiguous`, `aborted`, `error`), and does not fail the call.

`call_ended` is posted on the existing path and is not held for this read. If
the file is not ready yet the alert says `transcript_status=pending`. The same
process then waits up to 25s for `transcript.json` and posts `call_transcript`
with the same `post_alert_event` helper, webhook URL, key, and spool. The hub
process does not post that event: `gsm2computer-hub.service` has no webhook
URL, and adding one would be a second copy of the secret. The wait runs in a
daemon thread on that process. Restarting the unit during the wait drops the
follow-up; `transcript.json` is still written for the portal, and
`call_transcript` is not retried.

On a normal hangup that process is `gsm2computer-call-end-watch.service`
(`call_end_watch.py`). When adb did not fire within 25s,
`gsm2computer-hub-call-watch.service` (`hub_call_watch.py`) posts both events
and already loads `alert-webhook.conf`. No cup config change is required when
`call_ended` alerts already arrive, because `call_transcript` leaves from that
same unit. If a machine's call-end unit does not have the webhook env, copy
the existing `alert-webhook.conf` onto
`~/.config/systemd/user/gsm2computer-call-end-watch.service.d/` and
`systemctl --user daemon-reload` that unit. Do not also install it on
`gsm2computer-hub.service`.

`patchbay status --json` must expose a loopback CDP base (`cdp_http`,
`cdp_url`, or `cdp_port`). Otherwise set `GSM2COMPUTER_GROKBOT_CDP` to
`http://127.0.0.1:<port>`. The reader will not click the OpenClaw Talk browser.

```bash
jq -r '.turns[] | [.speaker, (.timestamp // "-"), .text] | @tsv' \
  ~/gsm2computer-calls/<call-id>/transcript.json
```

`speaker` is `caller` or `assistant`. `timestamp` is null when the page had
none. The `call_ended` payload (adb watcher and hub watcher) and the follow-up
`call_transcript` event include `transcript.status`, `transcript.path`,
`transcript.turns`, `transcript.error`, and a short `transcript.preview`.
`GET /portal/api/calls` adds the same pointer when the file is on disk,
including for a call row posted before the read finished. The Calls card reads
that pointer from the bundle `hub/portal/dist/index.html` loads.

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
| GSM2COMPUTER_GROKBOT_CDP | unset; loopback URL from `patchbay status --json` |
| GSM2COMPUTER_GROKBOT_TRANSCRIPT_TIMEOUT_S | 12 |
| GSM2COMPUTER_GROKBOT_TRANSCRIPT_ABORT_S | 1 (max extra wait before the next switch) |
| GSM2COMPUTER_GROKBOT_TRANSCRIPT_SLACK_S | 30 (only after hangup; no pre-call match, and no match without a start time) |
| GSM2COMPUTER_GROKBOT_TRANSCRIPT_FOLLOWUP_S | 25 (how long the call_ended process waits before posting call_transcript) |
| GSM2COMPUTER_CALL_RECORD_KEEP | 20 call directories, including the downlink and transcript |

## Deploy / rollback (only while /health idle)

Deploy: `ops/grokbot/grokbot.conf` -> `~/.config/systemd/user/gsm2computer-hub.service.d/`,
`systemctl --user daemon-reload && systemctl --user restart gsm2computer-hub`.

Rollback to OpenClaw:

    rm ~/.config/systemd/user/gsm2computer-hub.service.d/grokbot.conf && systemctl --user daemon-reload && systemctl --user restart gsm2computer-hub
