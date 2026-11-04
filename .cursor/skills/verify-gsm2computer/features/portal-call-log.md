# Portal call log

After a bridged call the phone posts a row to the hub. The operator opens Calls in the portal and sees it. Playback looks for an mp3 under the call-record directory. This is not the list inside the Android Calls tab.

## Sub-features

- Ingest: `POST /calls` (`CallLogUploader` / `HubEndpoints.callJson`) with `direction` (`in` or `out`), `number`, `started_at` (ISO-8601), `duration_sec`. Optional `switchboard_mode`, `session_id`, `tap_summary`. Missing mode and session are filled from the live hub mode and the last call tap when the hub has them.
- List: `GET /portal/api/calls`, newest `started_at` first.
- Recording: `GET /portal/api/calls/{id}/recording` serves `audio/mpeg` with `Range` when a file exists. Otherwise 404 `no recording`.
- Portal Calls page paints one `button.call-card` per row (`data-call-id`). Clicking toggles playback of that recording.

## How to get to it (user POV)

1. A call is bridged and then hangs up. The phone uploads the record.
2. Open the portal, Calls in the top nav (`/portal/calls`).
3. The card shows Incoming or Outgoing, the number, the duration, and the start time. Empty copy is `Mixer idle. No GSM or simulator legs bridged.`
4. Press the card to play the OpenClaw recording, when one was captured.

## Driving it with curl

The phone posts this shape with no recording on disk. A fresh stand-in has `mode` null and no last tap, so the stored row keeps the fields you send.

```bash
# shellcheck disable=SC1091
source "$GSM2COMPUTER_VERIFY_HOME/instance.env"
EVIDENCE="$GSM2COMPUTER_VERIFY_EVIDENCE"

curl -sS -D "$EVIDENCE/call.headers" -o "$EVIDENCE/call.json" \
  -H 'Content-Type: application/json' \
  -d '{"direction":"in","number":"+15551212","started_at":"2026-11-04T16:05:00Z","duration_sec":12}' \
  "$GSM2COMPUTER_HUB_BASE/calls"

curl -sS -o "$EVIDENCE/calls.json" \
  "$GSM2COMPUTER_HUB_BASE/portal/api/calls"
```

Pass: `call.headers` is `201`, `call.json` has `ok: true` and `call.number` `+15551212`, `call.direction` `in`, `call.duration_sec` 12. `calls.json` is a non-empty array whose first matching row has that number.

Then the recording the UI would request:

```bash
id=$(python3 -c 'import json; print(json.load(open("'"$EVIDENCE"'/call.json"))["call"]["id"])')
curl -sS -D "$EVIDENCE/recording.headers" -o "$EVIDENCE/recording.body" \
  "$GSM2COMPUTER_HUB_BASE/portal/api/calls/$id/recording"
```

On a stand-in with an empty `GSM2COMPUTER_CALL_RECORD_DIR`, that response is 404 `no recording`. That is the honest result. Do not drop the playback check to make the journey green. A pass of playback needs a real `openclaw.mp3` (or `openclaw-relay.mp3` / `gsm.mp3`) in a directory `resolve_call_recording` will accept (session id, tap id, or a `started_at` folder prefix).

Portal selectors if you open `$GSM2COMPUTER_HUB_BASE/portal/calls`: nav link Calls, then `button.call-card[data-call-id="<id>"]`. Reject the page when it shows `Local mock`.

## Gotchas

- On-phone direction in `CallLogStore` is `IN` / `OUT`. The hub JSON uses `in` / `out`. Posting `IN` stores `IN`; the portal treats anything other than `out` as incoming, so it still looks incoming, but it is not what the phone sends.
- `number` is required. A blank number is HTTP 400.
- This does not add a row to the Android Calls tab. That list is `CallLogStore` on the phone, written only when the bridge handles a call.
- The card's play control 404s until a capture exists. Call capture (`GSM2COMPUTER_CALL_RECORD`) runs inside a live audio bridge, which this VM does not have.
- `formatFixturePeer` labels numbers that contain `555` as `Mock …` in the UI. The JSON `number` is still the value you posted. Read `calls.json` for the stored number.
