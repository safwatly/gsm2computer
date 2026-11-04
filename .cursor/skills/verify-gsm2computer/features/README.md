# Feature map

What a person can do in gsm2computer, and how a cloud agent drives it. The phone is not in the VM. Journeys that need it stay written as the real journey; the gap is in Gotchas, not a weaker stand-in treated as a pass.

| Feature | Person | Harness on a cloud VM |
|---|---|---|
| [SMS forward and outbox](sms-forward-outbox.md) | Text the gateway SIM; read and reply in the portal; the phone sends the reply | `curl` against the isolated hub. This is the journey [SKILL.md](../SKILL.md) runs. |
| [Hub call session](hub-call-session.md) | Dial the gateway; one live call; hub answers with `session.updated` | Unit tests always. Live WebSocket only when PipeWire buses exist. |
| [Portal call log](portal-call-log.md) | After a bridged call, open Calls in the portal | `curl` `POST /calls` then `GET /portal/api/calls`. Does not create the on-phone log. |
| [Android Calls and Dialer](android-calls-dialer.md) | Open the bridge app, dial, read the call log | Headless API 34 emulator: `adb` taps and `screencap`. System call-log rows stay invisible until [PR #23](https://github.com/safwatly/gsm2computer/pull/23). |
| [Diagnostics and health](diagnostics-health.md) | Settings → Device Info on the phone; `GET /health` for the hub | `GET /health` is the doctor. Device Info opens on the emulator and shows emulator radio, not the moto g84. |

Start with [SKILL.md](../SKILL.md) (launch, doctor, evidence, cleanup).
