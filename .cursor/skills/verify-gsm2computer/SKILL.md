---
name: verify-gsm2computer
description: >-
  Verify gsm2computer, the rooted-Android GSM bridge (app/) plus its Python hub
  (hub/), by running the hub unit tests and driving an isolated local hub over
  HTTP: /health, SMS ingest and outbox, portal threads, and the portal call
  log. Use before declaring hub, portal, SMS, or call-slot work done. Call
  answering, call audio, Bluetooth routing, and the Android Dialer/Calls UI
  need the physical phone and are not verified by this skill.
---

# Verify gsm2computer

Cloud agents do not have the rooted Motorola moto g84. What they can drive is the hub process, its HTTP API, the portal SQLite store, and the Python unit tests. The phone app is a separate surface: see [Not verifiable here](#not-verifiable-here).

Run commands from the repo root. The helper is [scripts/instance.sh](scripts/instance.sh).

## Fast checks

These do not start the hub and do not need the phone.

```bash
python3 -m unittest discover -s hub -p 'test_*.py' -v
```

That is the CI hub job (`.github/workflows/ci.yml`). It includes `hub/tests/test_portal.py`. Stdlib only; Python 3.12 is enough (`audioop` is still in 3.12 and is imported by `hub/hub.py`).

`test_ws_backpressure.py` imports `hub.py`, and that import creates `hub/portal.sqlite` when `GSM2COMPUTER_PORTAL_DB` is unset. The file is gitignored. Point the variable at a temp file so the worktree stays clean:

```bash
GSM2COMPUTER_PORTAL_DB="$(mktemp /tmp/gsm2computer-portal.XXXXXX.sqlite)" \
  python3 -m unittest discover -s hub -p 'test_*.py' -v
```

Portal UI unit tests (no Vite install; Node's test runner):

```bash
node --test hub/portal/src/*.test.js
```

Run that from the repo root. The files import each other as ES modules.

Android JVM tests, ktlint, detekt, lint, and `assembleRelease` match CI:

```bash
./gradlew ktlintCheck detekt lint test assembleRelease --no-daemon
```

That needs JDK 17+ and an Android SDK with `platforms;android-34` (`sudo ./setup.sh`, or `ANDROID_HOME` plus a debug keystore at `~/.android/debug.keystore` as CI creates). JDK 21 is enough for Gradle 8.5 to start. With no SDK the build stops before any test:

```text
SDK location not found. Define a valid SDK location with an ANDROID_HOME
environment variable or by setting the sdk.dir path in your project's
local properties file
```

`app/src/androidTest` (`SmokeInstrumentationTest`) needs a device or emulator and `./gradlew connectedAndroidTest`. There is no emulator image in a typical cloud VM, and `adb` is not assumed.

## Launch

One isolated hub, empty data dir, loopback address. Production systemd binds `GSM2COMPUTER_HUB_HOST=100.101.181.110` port `8787` with `GSM2COMPUTER_OPENCLAW_TALK=webrtc-ui` (`hub/gsm2computer-hub.service`). This stand-in does not do that. It listens on `127.0.0.1` and an unused port (default **18787**), stores SQLite and logs only under the data dir, and sets `GSM2COMPUTER_OPENCLAW_TALK=off` so `/health` does not probe Chromium.

```bash
export GSM2COMPUTER_VERIFY_HOME="$(mktemp -d /tmp/gsm2computer-verify.XXXXXX)"
export GSM2COMPUTER_VERIFY_EVIDENCE="$GSM2COMPUTER_VERIFY_HOME/evidence"
mkdir -p "$GSM2COMPUTER_VERIFY_EVIDENCE"
.cursor/skills/verify-gsm2computer/scripts/instance.sh launch
```

Ready looks like:

```text
ready http://127.0.0.1:18787 pid <pid> home /tmp/gsm2computer-verify.XXXXXX
```

and `GET /health` has already returned JSON. The log line `listening on` is in `$GSM2COMPUTER_VERIFY_HOME/hub.log`. State for later commands is `$GSM2COMPUTER_VERIFY_HOME/instance.env` (also recorded in `/tmp/gsm2computer-verify.current`).

`launch` creates `portal.sqlite` on startup (empty schema). It does not delete an existing home. For a clean database, point `GSM2COMPUTER_VERIFY_HOME` at a new empty directory.

A second instance needs its own home and port:

```bash
GSM2COMPUTER_VERIFY_HOME="$(mktemp -d /tmp/gsm2computer-verify.XXXXXX)" \
GSM2COMPUTER_HUB_PORT=18788 \
  .cursor/skills/verify-gsm2computer/scripts/instance.sh launch
```

Do not share `portal.sqlite`. Do not use port 8787 unless you have confirmed nothing production-shaped is bound there.

## Doctor

One read-only check. It does not POST, and it does not claim the call slot.

```bash
.cursor/skills/verify-gsm2computer/scripts/instance.sh doctor
```

Worth driving only when all of these are true:

- the recorded pid is alive and its command line is `python` running `hub.py`
- `GET /health` is HTTP 200, `ok` is true, `openclaw_talk` is `off`, `call.busy` is false
- `GET /portal/api/threads` is a JSON array (empty on a fresh database)

Stdout on success:

```text
worth driving http://127.0.0.1:<port> threads=<n> talk=off call.busy=false pid=<pid>
```

Anything else prints `not worth driving:` and exits non-zero. A busy slot, a dead pid, or `openclaw_talk` other than `off` means this is not the isolated stand-in (a webrtc-ui hub will try the Talk CDP probe on `/health`).

Save the line with the evidence. Doctor itself does not write a file:

```bash
.cursor/skills/verify-gsm2computer/scripts/instance.sh doctor | tee "$GSM2COMPUTER_VERIFY_EVIDENCE/doctor.txt"
```

## Drive

Source the instance file so `GSM2COMPUTER_HUB_BASE` is the process you launched:

```bash
# shellcheck disable=SC1091
source "$GSM2COMPUTER_VERIFY_HOME/instance.env"
```

Map of what to drive: [features/README.md](features/README.md).

### SMS forward and outbox (the journey to run)

This is the phone's HTTP contract (`SmsForwarder`, `SmsOutboxPoller`) and the portal compose path. It does not send a radio SMS. Bodies must not be `STATUS` or `MODE …`. Those call the switchboard, and without `pw-link` the POST returns HTTP 500 after the row is stored.

```bash
# shellcheck disable=SC1091
source "$GSM2COMPUTER_VERIFY_HOME/instance.env"
EVIDENCE="$GSM2COMPUTER_VERIFY_EVIDENCE"
mkdir -p "$EVIDENCE"

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

python3 - "$EVIDENCE/outbox.json" <<'PY'
import json, sys
items = json.load(open(sys.argv[1]))["items"]
if len(items) != 1:
    raise SystemExit(f"expected one outbox item, got {items!r}")
open(sys.argv[1] + ".id", "w").write(items[0]["id"])
PY

curl -sS -o "$EVIDENCE/ack.json" \
  -H 'Content-Type: application/json' \
  -d '{"status":"sent"}' \
  "$GSM2COMPUTER_HUB_BASE/sms/outbox/$(cat "$EVIDENCE/outbox.json.id")/ack"

curl -sS -G --data-urlencode 'peer=+15551212' \
  -o "$EVIDENCE/messages.json" \
  "$GSM2COMPUTER_HUB_BASE/portal/api/messages"

curl -sS -o "$EVIDENCE/outbox-after.json" \
  "$GSM2COMPUTER_HUB_BASE/sms/outbox"

cp "$GSM2COMPUTER_HUB_LOG" "$EVIDENCE/hub.log"

python3 - "$EVIDENCE" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])

def load(name):
    return json.loads((root / name).read_text())

def status_of(name):
    line = (root / name).read_text().splitlines()[0]
    return int(line.split()[1])

sms = load("sms.json")
assert status_of("sms.headers") == 200, sms
assert sms["ok"] is True
assert sms["sms"]["from"] == "+15551212"
assert sms["sms"]["body"] == "hello from verify"

threads = load("threads.json")
assert any(t["peer"] == "+15551212" for t in threads), threads

send = load("send.json")
assert status_of("send.headers") == 201, send
assert send["ok"] is True
assert send["to"] == "+15551212"
assert send["body"] == "reply from portal"
assert send["message"]["status"] == "queued"

outbox = load("outbox.json")
assert outbox["ok"] is True and len(outbox["items"]) == 1
item = outbox["items"][0]
assert item["to"] == "+15551212" and item["body"] == "reply from portal"
assert item["status"] == "sending"

ack = load("ack.json")
assert ack["ok"] is True and ack["item"]["status"] == "sent"

messages = load("messages.json")
inbound = [m for m in messages if m["direction"] == "in"]
outbound = [m for m in messages if m["direction"] == "out"]
assert len(inbound) == 1 and inbound[0]["body"] == "hello from verify"
assert inbound[0]["status"] == "received"
assert len(outbound) == 1 and outbound[0]["body"] == "reply from portal"
assert outbound[0]["status"] == "sent"

again = load("outbox-after.json")
assert again["items"] == [], again
print("sms journey ok")
PY
```

Passing that checker is the journey. `hub.log` should also contain an `sms` line with `hello from verify`.

`GET /sms/outbox` leases pending rows as `sending`. A second poll is empty. Ack with `{"status":"sent"}` or `{"status":"failed","error":"..."}`.

Portal selectors, if you also open the UI the hub serves at `$GSM2COMPUTER_HUB_BASE/portal/messaging` (built files in `hub/portal/dist`, same origin, no Vite proxy):

- nav link text `Messaging` (`a.cockpit-link`)
- thread button `[data-peer="+15551212"]`
- compose form `#compose`, `textarea[name=body]`, submit `Send`

Reject the screen as proof when it shows `Local mock`. `getThreads` / `getHealth` fall back to `hub/portal/src/mocks/` when the fetch fails, and the shell then renders that badge. The JSON files above are the proof.

`cd hub/portal && npm run dev` serves port **5174** and proxies `/health`, `/portal/api`, and `/switchboard` to `http://127.0.0.1:8787` only (`hub/portal/vite.config.js`). It does not see this stand-in on 18787. Use the hub's `/portal/` or change that proxy. Do not `npm install` unless you are changing the PWA; the committed `hub/portal/dist` is what `hub.py` serves.

## Evidence

Write under `$GSM2COMPUTER_VERIFY_EVIDENCE` (default: the data dir's `evidence/` folder).

| File | What it proves |
|---|---|
| `doctor.txt` | Instance was idle and the portal store answered before the journey |
| `sms.json`, `sms.headers` | Phone-shaped `POST /sms` persisted |
| `threads.json` | Operator thread list contains `+15551212` |
| `send.json`, `send.headers` | Portal compose queued an outbox row (HTTP 201, status `queued`) |
| `outbox.json` | Phone poll leased that row (`sending`) |
| `ack.json` | Phone ack marked it `sent` |
| `messages.json` | Inbound `received` and outbound `sent` in the same thread |
| `outbox-after.json` | The lease was not still pending |
| `hub.log` | Hub log line for the inbound SMS |

Proof standard: exercise the real phone and portal HTTP path, keep the request and the state that followed, and check the side effect (thread rows and outbox status), not only the status code. Mock only where production already mocks: the portal's JSON fixtures are a fallback when the hub is down, so they are not evidence. Do not point the journey at `hub/portal/src/mocks/`.

## Cleanup

```bash
.cursor/skills/verify-gsm2computer/scripts/instance.sh stop
```

That sends `SIGTERM` to the pid in `instance.env`, then `SIGKILL` if it is still alive after about five seconds. It kills that pid only when `/proc/<pid>/cmdline` contains `python` and `hub.py`. It does not `pkill` by name, and it does not delete the data dir, `portal.sqlite`, `hub.log`, or the evidence directory.

After stop, the evidence files must still be there:

```bash
test -s "$GSM2COMPUTER_VERIFY_EVIDENCE/messages.json"
test -s "$GSM2COMPUTER_VERIFY_EVIDENCE/doctor.txt"
```

A failed attempt still ends with `stop` so the next launch is not blocked by a live pid. Leave the evidence until you have read it. Delete the home only after the pid is gone, and only when you want an empty database.

## Helpers

| Script | Role |
|---|---|
| `.cursor/skills/verify-gsm2computer/scripts/instance.sh launch` | Start one hub in the empty data dir and wait until `/health` returns |
| `.cursor/skills/verify-gsm2computer/scripts/instance.sh doctor` | Read-only "worth driving" check |
| `.cursor/skills/verify-gsm2computer/scripts/instance.sh stop` | Stop that pid and keep evidence |

The SMS curls live in this file, not in the helper, so the phone payload stays visible.

## Not verifiable here

Do not report these as passed because the hub journey passed.

- **Answering a GSM call.** In-call overlay `btnInCallAnswer` / `btnInCallReject` / `btnInCallEnd` on `activity_main.xml`. Needs the phone as the default dialer, with a SIM. No emulator in this repo's cloud path.
- **Call audio injection and capture.** Privileged `AudioRecord`, uplink injection back into the GSM call, and the PipeWire graph (`gsm_bus`, `phone_uplink`, `openclaw_bus`, `pw-cat` by object serial). `pactl` and `pw-link` are not on a stock cloud image. A WebSocket to `/loopback` is accepted (`101 Switching Protocols`) and then dropped before `session.updated`. The hub log is `FileNotFoundError: [Errno 2] No such file or directory: 'pw-link'` from `parse_switchboard_links` inside `set_switchboard_mode`, reported as `Unhandled exception in client_connected_cb`. `pipewire_handshake_failed` is not sent: that handler only catches `RuntimeError` and `PipewireLinkError` around `PipewireBridge.start`, and this failure is earlier. The slot is released; doctor still reports `call.busy` false.
- **Simulator as a handset.** `simulator/scripts/validate-ws.mjs` and the portal Simulator tab (`#start-btn`, `#tone-mode`) need that same handshake, then a mic or a tone. `npm run validate` is not a pass on this stand-in.
- **OpenClaw Talk.** Default `webrtc-ui` needs the dedicated Chromium profile, `DISPLAY`, and CDP `127.0.0.1:9222` (`hub/TALK_WEBRTC.md`). This stand-in forces `GSM2COMPUTER_OPENCLAW_TALK=off` so a missing browser fails the doctor instead of hanging a CDP probe. Do not flip it on to "make Talk pass."
- **Bluetooth and in-call speaker routing** (`btnInCallSpeaker`). Device audio policy; see `docs/VOICE_CALL_SILENCING_INVESTIGATION.md`.
- **Android Dialer and Calls tabs**, including the on-phone call log (`CallsFragment`, `CallLogStore`). There is no UI automation. [PR #23](https://github.com/safwatly/gsm2computer/pull/23) (system call log on the Calls tab) is not merged here; the screen on this tree is the bridge's own log. JVM tests do not open it.
- **Radio SMS.** `SmsOutboxPoller` calls `SmsManager.sendTextMessage` before it acks. The journey above acks `sent` the way the phone does after that call returns. It does not prove a message left the SIM.
- **Tailscale URL and production bind.** Default hub URL `http://hub.mining-ling.ts.net:8787` and host `100.101.181.110` are the operator's machine, not this VM.
- **Switchboard audio.** `GET /switchboard/state` shells out to `pw-link`. Without it the response is HTTP 500 `{"ok": false, "error": "internal error"}`, not a soft `ok: false` body. The portal Switchboard page then paints the local mock (`Roster local`), and a preset click looks applied. SMS bodies `STATUS` and `MODE <preset>` are stored and then hit the same `pw-link` crash, so `POST /sms` returns that 500 even though the thread row exists. Trust the messages list, not the status code, for those bodies. Do not use them for the SMS journey above.
