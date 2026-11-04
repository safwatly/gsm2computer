# Diagnostics and health

Two different checks share this name. The hub's `GET /health` is what the phone, the e2e watcher, and this skill's doctor use. The Android Device Info dialog is a local readout on the handset.

## Sub-features

- `GET /health` JSON: `ok`, `mode` (last switchboard preset, or null), `openclaw_talk` (`webrtc-ui`, `relay`, or `off`), `call` (slot snapshot from `CallSlot.snapshot`). After a captured call, `last_call_tap` is included. With `openclaw_talk=webrtc-ui`, `talk` is the Chromium probe (`cdp`, `browser_pid`, page state) and is capped at about 2.5 seconds.
- Portal Overview (`/portal/` or `/portal`) reads that payload for the Hub / Talk path / PipeWire lamps. Usage figures fall back to `hub/portal/src/mocks/usage.json` when `/health` has no `usage` object. The stand-in health body has no usage, so the token card is a fixture even when the hub lamp is real.
- Android: Settings tab, info button `btnInfo`, dialog title `Device Info`, button `btnCheckSupport` labeled `Check Gateway Support`. Results land in `checkResultsContainer` (permissions, default dialer, capture source). Close is the dialog's positive button.

## How to get to it (user POV)

Hub, from any browser on the tailnet: open the portal. Overview is the first nav item. A green Hub lamp means `health.ok`. The Talk path lamp is the `openclaw_talk` string.

Phone: open the app, Settings (right-hand tab), tap the info icon, tap Check Gateway Support. Each row is a pass or fail for that device. This is how you see whether the handset can be the default dialer and capture call audio.

## Driving it with curl

Doctor is this check for the stand-in (`openclaw_talk` must be `off`, `call.busy` false). To capture the body yourself:

```bash
# shellcheck disable=SC1091
source "$GSM2COMPUTER_VERIFY_HOME/instance.env"
curl -sS -D "$GSM2COMPUTER_VERIFY_EVIDENCE/health.headers" \
  -o "$GSM2COMPUTER_VERIFY_EVIDENCE/health.json" \
  "$GSM2COMPUTER_HUB_BASE/health"
```

Pass for the stand-in: HTTP 200, `ok` true, `openclaw_talk` `off`, `call.busy` false, `call.session_id` null.

Portal Overview selectors at `$GSM2COMPUTER_HUB_BASE/portal/`: nav link Overview, heading `Hub status`. The Talk path value on this stand-in is `off` (the lamp is the warning color unless the value is `webrtc-ui`). Ignore the Token / usage card; it is the mock file when health has no usage.

There is no command that opens Device Info. The dialog runs on the phone. JVM tests do not inflate it. `DiagnosticsController` is only referenced from `SettingsFragment`.

## Gotchas

- Production `/health` with `webrtc-ui` contacts CDP when port 9222 is open. The probe has a 2.5s cap, but a cloud agent that launches the hub without `GSM2COMPUTER_OPENCLAW_TALK=off` is not the stand-in doctor expects.
- `mode` stays null until something runs the switchboard. A null mode is not a failed hub.
- The Overview usage card is a fixture on every hub that does not add a `usage` object. Do not treat those numbers as live token counts.
- Switchboard `GET /switchboard/state` is a different endpoint. It runs `pw-link -l`. Without `pw-link` the response is HTTP 500 `{"ok": false, "error": "internal error"}`. The routing page catches that and paints the local roster (`Roster local`), not a live PipeWire graph.
- Device Info cannot be checked here. Default-dialer and capture-source rows need the rooted phone.
