# Hub drop-ins

Copy a file into `~/.config/systemd/user/gsm2computer-hub.service.d/`, then
`systemctl --user daemon-reload` and restart `gsm2computer-hub` while
`/health` shows `busy: false`.

## Test tone

`test-tone.conf` sets `GSM2COMPUTER_TEST_TONE=1`. The flag is off unless that
line is present. Each call then starts with about 2 s of downlink before bus
audio: 250 ms of 1 kHz, 250 ms of silence, 250 ms of 1 kHz, then silence
through 2 s. Frames are the normal 20 ms μ-law `response.output_audio.delta`
path. The phone's own beep is a single 0.5 s 1 kHz tone.

```bash
mkdir -p ~/.config/systemd/user/gsm2computer-hub.service.d
cp hub/ops/test-tone.conf ~/.config/systemd/user/gsm2computer-hub.service.d/test-tone.conf
systemctl --user daemon-reload && systemctl --user restart gsm2computer-hub
```

Remove that file and restart the unit to turn the tone off.

## Grok Bot CDP

`grokbot/grokbot-cdp.conf` is the cup drop-in that was only on the server.
It sets `GSM2COMPUTER_GROKBOT_CDP=http://127.0.0.1:9230` for the post-call
transcript read. Install it as
`~/.config/systemd/user/gsm2computer-hub.service.d/grokbot-cdp.conf`.
