# Loopback call audio

## Sub-features

- WebSocket upgrade on `/loopback`.
- Hub replies `session.updated` with `session.entrance`.
- Client sends `input_audio_buffer.append` (`audio/pcmu`, 8 kHz). Hub echoes `response.output_audio.delta` with the same payload.
- `pw-cat` playback into `gsm_bus` still has to link or the handshake returns an error event and the echo never starts.

## How to get to it (user POV)

A caller, or the simulator's loopback mode, connects to the hub WebSocket and speaks. On `/loopback` the hub sends that audio back on the same socket instead of through OpenClaw.

## Driving it with this skill

The entrance drive places three `/loopback` calls: public with the device token, Tailscale with only `Authorization: Bearer hub-token`, and Tailscale with the device token. Each must echo the frame it was sent. A fourth call goes through Caddy's TLS listener and must report `call.path=public`.

```bash
.cursor/skills/verify-gsm2computer/scripts/launch-hub.sh
python3 .cursor/skills/verify-gsm2computer/scripts/drive-entrances.py
```

Evidence keys: `public-loopback`, `tailscale-legacy-loopback`, `tailscale-token-loopback`, `caddy-loopback` in `entrances.json`.

## Gotchas

- Only one call can be up. The script waits until `/health` `call.busy` is false before the next upgrade. A 409 means the previous socket's `finally` has not released `CallSlot`.
- `GSM2COMPUTER_OPENCLAW_TALK=off` in this launch. A production hub with `webrtc-ui` will not loopback-echo; it will start Talk. Don't treat a green loopback as a GSM call.
- `pw-link` has to see `gsm_bus`. If `pactl info` is pointed at a different PipeWire, `hub/setup-audio-bus.sh` creates sinks the hub cannot open.
- `pw-cat --target` does not link itself. WirePlumber has to be running. On a VM with no systemd-logind, stock WirePlumber 0.4 exits immediately and the handshake fails with `pipewire_handshake_failed`. `launch-hub.sh` starts WirePlumber with the logind plugin off in that case.
- Real GSM uplink is PCM at the HAL rate ([ADR 0005](../../../../docs/adr/0005-native-pcm-hub-wire.md)). These frames omit that on purpose: `/loopback` still accepts μ-law when `format` is `audio/pcmu`.
