# ADR 0009: Asterisk AudioSocket gateway for the EC25

- **Status:** accepted
- **Date:** 2026-11-17

## Context

The rooted Moto g84 answers the Orange Egypt SIM and bridges the call to the hub over the phone WebSocket. On that handset the voice agent's audio cannot be injected into the GSM uplink, so the caller hears silence. That injection path is not getting fixed on the Moto.

The replacement is a Quectel EC25-E (voice, USB audio) on a Raspberry Pi in Egypt. The SIM moves into the modem. The Pi is on the same tailnet as cup. The hub on cup, the patchbay, and the voice agent stay where they are.

The Pi must not become a second call path that can hang up a live Moto call, and the Moto bridge has to keep working when the new path is switched off.

## Decision

**Media is Asterisk AudioSocket. Signalling is AMI.** Both are TCP.

AudioSocket is the Asterisk application that sends one call's audio to a TCP peer as length-prefixed 8 kHz signed-linear frames, and accepts the same frames back. The EC25 UAC stream is already 8 kHz 16-bit mono. The hub resamples that PCM to the 48 kHz patchbay the way it resamples the phone (ADR 0005). One TCP connection per call, no RTP ports, no codec negotiation.

SIP and ARI `externalMedia` were set aside because both put the audio in RTP. That is a second UDP path across the tailnet, plus a SIP stack or a Stasis app on the hub, for a single handset. AudioSocket is the whole media contract.

The hub does not talk to the modem. It listens for AudioSocket and opens the **same phone WebSocket** the Moto opens (`client.audio`, `input_audio_buffer.append`, `response.output_audio.delta`, close `1000`). PipeWire, Talk, the call slot, and `/health` `call.busy` are unchanged. A gateway call is busy because it holds that slot.

AMI on the Pi is how the hub originates an outbound call (`Quectel/ec25/<number>` into the AudioSocket dialplan), how it sends outbox SMS (`quectel sms`), and how inbound SMS arrives (`UserEvent GsmSms`). The dialplan also POSTs `/sms` with the same `clientId`. A repeat is the existing duplicate and does not page the webhook twice.

A new GSM leg is refused when the slot is busy, a leg is already reserved, or the phone WebSocket returns 409. The refusal hangs up that AudioSocket only. It does not send AMI `Hangup` for the live channel and it does not abort the slot (ADR 0004). AudioSocket audio is accepted only for a uuid the hub just returned from `/gateway/admit` or `/gateway/dial`. The reservation lasts 75 seconds, which covers the 60 second originate. A connection with any other uuid is hung up.

The path is off unless `GSM2COMPUTER_ASTERISK_GATEWAY` is set. Unset, the process does not bind AudioSocket, does not dial AMI, and does not lease the SMS outbox. `/gateway/*` is 404. The public entrance does not serve those routes.

## Consequences

- Deploy the hub before pointing the Pi at it. An AudioSocket client against a hub that is still μ-law-only would be the same class of mistake as PCM into an old `pw-cat` (ADR 0005). This hub already resamples.
- Cutover is an env change on cup plus moving the SIM. `hub/ops/cutover.sh` stays the way the hub process is restarted. This repo does not write the live unit file.
- The ADB call-end watcher watches the phone. Gateway calls post their own `call_ended` with `source=asterisk_gateway`.
- Inbound SMS uses `POST /sms`, including `GSM2COMPUTER_SMS_WEBHOOK_URL`. Outbound portal rows go to the modem only while the gateway is on and AMI is up. Stop the Moto bridge first so it is not also leasing the outbox.
- Voice is CS (the same 3G voice the Moto uses). This does not turn on VoLTE.
- Runbook: [`ops/asterisk-gateway/README.md`](../../ops/asterisk-gateway/README.md).
