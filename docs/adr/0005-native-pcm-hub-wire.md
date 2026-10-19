# ADR 0005: Native PCM on the hub WebSocket; hub resamples to 48 kHz

- **Status:** accepted
- **Date:** 2026-09-06
- **Tickets:** SAF-11

## Context

ADR 0001/0002 put GSM audio on a WebSocket into PipeWire. The phone and simulator both spoke **8 kHz G.711 μ-law**. The PipeWire buses on safwat-eu are **48 kHz**. Hub `pw-cat` still ingested μ-law at 8 kHz and let PipeWire stretch it.

Pixel `AudioRecord(VOICE_DOWNLINK)` will init at 48 kHz or 16 kHz when asked. Forcing 8 kHz + μ-law threw away whatever the HAL offered and added quantization grain. BCR opening the same downlink at 16 kHz matched the gateway pre-gain tap (same low band, no extra formants) — so a higher *request* does not invent bandwidth the modem tap does not have. It still means: take the best PCM the phone will give, and do not make the hub the thing that breaks.

Playback to the caller should be as good as we can receive. 48 kHz into `TYPE_TELEPHONY` is fine; the GSM uplink will band-limit. 8 kHz downlink was already intelligible.

The browser simulator stays 8 kHz μ-law and must keep working without a format field.

## Decision

**The hub owns rate and format.** Clients send what they have. Missing `format` / `rate` means `audio/pcmu` at 8000 (simulator, old phones).

| Side | Behavior |
|------|----------|
| Phone WS capture | Try AudioRecord at **48 kHz, then 16, then 8**. Send **16 kHz** s16le mono (downsample when the HAL is faster) as a binary `G2` frame. JSON `input_audio_buffer.append` remains valid for older builds. |
| Phone WS playback | Try AudioTrack at **48 kHz, then 16, then 8**. Accept PCM or μ-law deltas (`format` + `rate` on `response.output_audio.delta`). |
| Hub PipeWire | Always **48 kHz s16** `pw-cat` / `pw-record` on `phone_uplink` / `openclaw_bus.monitor`. Resample with `audioop.ratecv`. |
| Simulator | Unchanged: 8 kHz μ-law, no `client.audio`. |

`client.audio` `{input,output:{format,rate}}` tells the hub what will be sent and what the client can play. `session.updated.audio` still defaults to pcmu/8000 and advertises `preferred: {format: audio/pcm, rate: 16000, encoding: s16le}`.

UDP/SIP RTP is unchanged (G.711 / G.722). Pixel software gain is device calibration (`captureGain`), not part of this wire.

## Amendment (2026-10-19)

Build 330 sent 48 kHz PCM as base64 JSON (~1 Mbps). On a throttled uplink the phone's send queue filled in about 5 seconds and the socket relinked every ~11 seconds, so the hub heard only a few seconds of a one-minute call. Downlink was fine.

The phone now captures at the HAL rate and **sends 16 kHz mono s16le** (48 kHz is averaged down). Each frame is a binary WebSocket message with an 8-byte `G2` header (version, format, rate) plus the PCM. That is about **260 kbps**. Downlink stays JSON PCM at the playback rate. `client.audio` still announces input and output. Missing `format` / `rate` on a JSON append is still 8 kHz μ-law, so the simulator and build 330 keep working. The hub resamples whatever arrives to the 48 kHz PipeWire bus.

`session.updated.audio.preferred.rate` is 16000. Old clients that ignore `preferred` are unchanged.

## Consequences

- Deploy hub and phone APK together. PCM into an 8 kHz μ-law `pw-cat` is noise.
- Simulator and loopback keep working because omitted fields are μ-law.
- Relay Talk (`feed_gsm_ulaw`) still wants 8 kHz μ-law; the hub converts PCM down when that mode is on.
- Call-tap stem names (`gsm-uplink-8k-mono`) are historical; WAV headers carry the real rate and ffmpeg already `aresample`s.
- A 48 kHz HAL init does **not** imply wideband far-end speech. Measure `CaptureTap` `meta.json` (`rate`, `hal_sample_rate`, `implied_hz`) before assuming the tap grew formants.
