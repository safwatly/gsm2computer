# SMS forward and outbox

The gateway phone posts inbound SMS to the hub. The operator reads the thread in the portal and replies. The phone polls the outbox and sends via `SmsManager`, then acks.

## Sub-features

- Inbound persist: `POST /sms` with `{from, body, receivedAt}` (`SmsForwarder`, `HubEndpoints.smsJson`). Hub stores the row and returns `{ok, sms}`.
- Thread list and history: `GET /portal/api/threads`, `GET /portal/api/messages?peer=`.
- Operator reply: `POST /portal/api/messages/send` with `{to, body}` queues `outbox` as `pending` and the message as `queued`.
- Phone poll: `GET /sms/outbox` leases pending rows to `sending` and returns `{ok, items: [{id, to, body, status}]}`.
- Ack: `POST /sms/outbox/{id}/ack` with `{status: sent}` or `{status: failed, error}`.
- Command SMS: body `STATUS` or `MODE <preset>` also runs the switchboard. That is a different journey (see Gotchas).
- Ops wake-up: SMS from `GSM2COMPUTER_OPS_SMS_NUMBERS` (default `+201280043725`) or a system-down body can POST the alert webhook. Unset `GSM2COMPUTER_ALERT_WEBHOOK_URL` is a no-op.

## How to get to it (user POV)

1. Someone texts the gateway SIM.
2. On a machine that can reach the hub, open the portal Messaging tab (`/portal/messaging`). The thread shows the inbound text. Empty copy is `No SMS on the wire. Pixel forwarder idle.`
3. Open the thread, type in the compose box, press Send.
4. Within about 10 seconds the gateway phone polls, sends the SMS, and the bubble leaves `queued`.

## Driving it with curl

Launch and doctor first ([SKILL.md](../SKILL.md)). Then the inbound, the thread, the reply, the poll, and the ack:

```bash
# shellcheck disable=SC1091
source "$GSM2COMPUTER_VERIFY_HOME/instance.env"
EVIDENCE="$GSM2COMPUTER_VERIFY_EVIDENCE"

curl -sS -D "$EVIDENCE/sms.headers" -o "$EVIDENCE/sms.json" \
  -H 'Content-Type: application/json' \
  -d '{"from":"+15551212","body":"hello from verify","receivedAt":"2026-11-04T16:00:00Z"}' \
  "$GSM2COMPUTER_HUB_BASE/sms"

curl -sS -o "$EVIDENCE/threads.json" \
  "$GSM2COMPUTER_HUB_BASE/portal/api/threads"

curl -sS -D "$EVIDENCE/send.headers" -o "$EVIDENCE/send.json" \
  -H 'Content-Type: application/json' \
  -d '{"to":"+15551212","body":"reply from portal"}' \
  "$GSM2COMPUTER_HUB_BASE/portal/api/messages/send"

curl -sS -o "$EVIDENCE/outbox.json" \
  "$GSM2COMPUTER_HUB_BASE/sms/outbox"

python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["items"][0]["id"])' \
  "$EVIDENCE/outbox.json" > "$EVIDENCE/outbox.json.id"

curl -sS -o "$EVIDENCE/ack.json" \
  -H 'Content-Type: application/json' \
  -d '{"status":"sent"}' \
  "$GSM2COMPUTER_HUB_BASE/sms/outbox/$(cat "$EVIDENCE/outbox.json.id")/ack"

curl -sS -G --data-urlencode 'peer=+15551212' \
  -o "$EVIDENCE/messages.json" \
  "$GSM2COMPUTER_HUB_BASE/portal/api/messages"

curl -sS -o "$EVIDENCE/outbox-after.json" \
  "$GSM2COMPUTER_HUB_BASE/sms/outbox"
```

The checker in SKILL.md is the pass condition: inbound `received`, outbound `sent`, second poll `items` empty.

Portal UI, only as a supplement to those files. Open `$GSM2COMPUTER_HUB_BASE/portal/messaging`.

- `a.cockpit-link` whose text is Messaging
- `[data-peer="+15551212"]`
- `#compose` `textarea[name=body]` and the Send button

If the page shows `Local mock`, the hub fetch failed and the fixtures in `hub/portal/src/mocks/` were painted. That screen is not evidence.

## Driving inbound SMS from the emulator

The curl above is the journey that does not need a handset. The app path also works on the emulator, because `SmsReceiver` listens for `SMS_RECEIVED` and `adb emu sms send` is delivered with `BROADCAST_SMS`.

The emulator reaches the host loopback as `10.0.2.2`, not `127.0.0.1`. Default `http://hub.mining-ling.ts.net:8787` does not resolve here (`SmsOutbox: Unable to resolve host`).

```bash
HUB_CONTROL_URL=http://10.0.2.2:18787 ./scripts/configure-bridge.sh --force
adb shell am force-stop com.gsm2computer.bridge
adb shell am start -W -n com.gsm2computer.bridge/.MainActivity
adb emu sms send +15551212 "hello from emulator"
```

Force-stop so the process reloads the prefs file. Proven: logcat `SmsReceiver: SMS received (19 chars) — forwarding`, and `GET /portal/api/messages?peer=+15551212` returned direction `in`, body `hello from emulator`, status `received`.

That does not lease the outbox or call `SmsManager`. Keep the curl journey for the reply and the ack.

## Gotchas

- `GET /sms/outbox` is a lease. Repeating it without an ack does not return the same item, and the row stays `sending`.
- A 10-digit `from` without `+` is stored as `+1` plus those digits (`normalize_e164`). The drive uses `+15551212` so the peer string is stable.
- Re-running the journey on the same `portal.sqlite` adds another inbound row. Doctor does not require an empty thread list. Use a new `GSM2COMPUTER_VERIFY_HOME` when you need one row.
- Body `STATUS` or `MODE` plus a preset (`openclaw`, `loopback`, `conference`, `clear`) is written to the thread, then `set_switchboard_mode` runs `pw-link`. Without `pw-link`, `POST /sms` returns HTTP 500 `{"ok": false, "error": "internal error"}` and the row is still in `GET /portal/api/messages`. Do not use those bodies for this journey, and do not treat that 500 as a lost SMS.
- The ack does not prove `SmsManager.sendTextMessage` ran. The phone only acks after that call. `adb emu sms send` proves inbound delivery into the hub. It does not prove a reply left a SIM.
- `npm run dev` in `hub/portal` proxies API calls to port 8787, not to the stand-in port.
