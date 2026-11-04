---
name: verify-gsm2computer
description: >-
  Verify gsm2computer, the rooted-Android GSM bridge (app/) plus its Python hub
  (hub/). Drive the isolated hub over HTTP (health, SMS, portal threads, portal
  call log) and, on a cloud VM with KVM, build the debug APK, boot a headless
  API 34 emulator, and screenshot Dialer, Calls, and Settings. Use before
  declaring hub, portal, SMS, or Android UI work done. Real GSM calls, call
  audio injection, Bluetooth routing, and root-only features need the physical
  phone and are not verified by this skill.
---

# Verify gsm2computer

Cloud agents do not have the rooted Motorola moto g84. What they can drive is the hub process, its HTTP API, the portal SQLite store, the Python unit tests, and — once [scripts/cloud-agent-android-sdk.sh](../../../scripts/cloud-agent-android-sdk.sh) has installed the SDK — the debug app on a headless emulator. Real radio, audio injection, and root stay in [Not verifiable here](#not-verifiable-here).

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

That needs JDK 17+ and an Android SDK with `platforms;android-34` and `build-tools;34.0.0`. On a cloud agent, [scripts/cloud-agent-android-sdk.sh](../../../scripts/cloud-agent-android-sdk.sh) installs those, the Gradle 8.5 wrapper cache, the emulator, and AVD `gsm2computer`. It writes gitignored `local.properties` (`sdk.dir=/opt/android-sdk`). JDK 21 is enough for Gradle 8.5. With no SDK the build stops before any test:

```text
SDK location not found. Define a valid SDK location with an ANDROID_HOME
environment variable or by setting the sdk.dir path in your project's
local properties file
```

`app/src/androidTest` (`SmokeInstrumentationTest`) runs on the emulator with `./gradlew connectedDebugAndroidTest`. See [Android emulator](#android-emulator).

## Android emulator

This VM can run a headless API 34 x86_64 emulator. `/dev/kvm` is `crw-rw----` owned by group `kvm` (gid 103, often unnamed until the setup script creates the group). The agent user is not in that group in the shell that ran `usermod`. Open the device with `sg kvm`. Boot was about 35 seconds with software GPU. There is no window; screenshots are `adb exec-out screencap`.

Do not put the emulator in the environment `start` script. It is heavy, and a failed boot must not be something every agent pays for. Boot it only when the change is in `app/`.

```bash
bash scripts/cloud-agent-android-sdk.sh
.cursor/skills/verify-gsm2computer/scripts/emulator.sh
```

The second command is a no-op when `emulator-5554` is already booted (`sys.boot_completed=1`). Otherwise it runs, under `sg kvm`:

```text
emulator -avd gsm2computer -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect -accel on -no-snapshot -no-metrics -ports 5554,5555
```

AVD `gsm2computer` is a Pixel 6 image (`google_apis` x86_64, API 34), 1080×2400. The setup script creates it and does not boot it.

Install and open the debug build. Grant `RECORD_AUDIO` before the first `am start`. `GatewayService` starts a microphone foreground service and crashes without that permission (`SecurityException` on `FOREGROUND_SERVICE_MICROPHONE` / `RECORD_AUDIO`).

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm grant com.gsm2computer.bridge android.permission.RECORD_AUDIO
adb shell pm grant com.gsm2computer.bridge android.permission.READ_CALL_LOG
adb shell pm grant com.gsm2computer.bridge android.permission.RECEIVE_SMS
adb shell pm grant com.gsm2computer.bridge android.permission.SEND_SMS
adb shell pm grant com.gsm2computer.bridge android.permission.CALL_PHONE
adb shell pm grant com.gsm2computer.bridge android.permission.READ_PHONE_STATE
adb shell cmd role add-role-holder android.app.role.DIALER com.gsm2computer.bridge
adb shell am start -W -n com.gsm2computer.bridge/.MainActivity
```

`cmd role add-role-holder` succeeds only while the package is installed. `./gradlew connectedDebugAndroidTest` uninstalls it when the run finishes, and the role command then fails with `Package does not qualify` / `Cannot get ApplicationInfo`. Reinstall before another UI pass.

Do not send `KEYCODE_BACK` before a screenshot. Back leaves `MainActivity` and the next `screencap` is the Nexus launcher, while the process stays alive. Bring the activity back with `am start -W`.

Dump the hierarchy and read `text` / `resource-id` before tapping. On this 1080×2400 image the proven centers were: digit `5` at `540 1088`, Calls tab `540 2263`, Settings tab `900 2263`, info button `btnInfo` at `880 212`, dialog `CLOSE` at `893 1812`. Re-dump after a font or layout change. Ids to require: `tvDialNumber`, `tabBtnDialer`, `tabBtnCalls`, `tabBtnSettings`, `tvCallsEmpty`.

What that drive showed on 2026-11-04:

- Dialer: hint `Enter number`, then `5551212` after tapping `5` `5` `5` `1` `2` `1` `2`.
- Calls, before any seed: `No calls yet`.
- Settings: title `GSM2Computer`, status `Ready for calls`, buttons `STOP` and `LOG`. The log line includes `RootShell` failing `su`. This image is not rooted.
- Device Info (`btnInfo`): title `Device Info`, GSM `T-Mobile` / `3G` / phone `+15551234567`, Wi-Fi `AndroidWifi`, local IP `10.0.2.16`, button `CHECK GATEWAY SUPPORT`. Those are emulator radio values, not the moto g84.
- `./gradlew connectedDebugAndroidTest --no-daemon`: `SmokeInstrumentationTest` 2 tests, 0 failures, on `gsm2computer(AVD) - 14`. The Gradle log still prints a `GatewayService` crash from the install that had not been granted `RECORD_AUDIO`. The test process itself passed.

System call log versus the Calls tab on this tree:

```bash
adb shell content insert --uri content://call_log/calls \
  --bind number:s:+15551212 --bind type:i:1 \
  --bind date:l:"$(adb shell date +%s%3N | tr -d '\r')" \
  --bind duration:l:42 --bind new:i:1
adb shell content query --uri content://call_log/calls --projection number:type:duration
```

The query returned `number=+15551212, type=1, duration=42`. The Calls tab still said `No calls yet`. This tree reads `CallLogStore` (app prefs), not `CallLog.Calls`. [PR #23 — Show the phone call log on the bridge Calls tab](https://github.com/safwatly/gsm2computer/pull/23) is the change that would display that row. Do not treat the empty tab as a failure of the insert, and do not claim PR #23's screen was exercised.

Emulator SMS into the isolated hub (the hub must already be launched; the emulator reaches the host as `10.0.2.2`):

```bash
HUB_CONTROL_URL=http://10.0.2.2:18787 ./scripts/configure-bridge.sh --force
adb shell am force-stop com.gsm2computer.bridge
adb shell am start -W -n com.gsm2computer.bridge/.MainActivity
adb emu sms send +15551212 "hello from emulator"
```

`SmsReceiver` is guarded by `android.permission.BROADCAST_SMS`, so a shell `am broadcast` is the wrong injection. `adb emu sms send` is the telephony path. Force-stop before the SMS so `SharedPreferences` reloads the file `configure-bridge.sh` wrote. Proven result: logcat `SmsReceiver: SMS received (19 chars) — forwarding`, and `GET /portal/api/threads` contained peer `+15551212` with `lastBody` `hello from emulator` (`status` `received`). The outbox send (`SmsManager`) is still the phone.

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
| `.cursor/skills/verify-gsm2computer/scripts/emulator.sh` | Boot AVD `gsm2computer` headless, or report that it is already booted |
| `scripts/cloud-agent-android-sdk.sh` | Install JDK, SDK 34, build-tools 34.0.0, emulator, system image, AVD, Gradle cache |

The SMS curls live in this file, not in the helper, so the phone payload stays visible.

## Not verifiable here

Do not report these as passed because the hub journey passed.

- **Answering a real GSM call.** In-call overlay `btnInCallAnswer` / `btnInCallReject` / `btnInCallEnd` on `activity_main.xml`. Needs the moto g84 as the default dialer, with a SIM. The emulator can hold `android.app.role.DIALER` and can show the dialer UI. It does not place a carrier call, and the green call button was not treated as a pass.
- **Call audio injection and capture.** Privileged `AudioRecord`, uplink injection back into the GSM call, and the PipeWire graph (`gsm_bus`, `phone_uplink`, `openclaw_bus`, `pw-cat` by object serial). The emulator is started with `-no-audio`. `pactl` and `pw-link` are not on a stock cloud image. A WebSocket to `/loopback` is accepted (`101 Switching Protocols`) and then dropped before `session.updated`. The hub log is `FileNotFoundError: [Errno 2] No such file or directory: 'pw-link'` from `parse_switchboard_links` inside `set_switchboard_mode`, reported as `Unhandled exception in client_connected_cb`. `pipewire_handshake_failed` is not sent: that handler only catches `RuntimeError` and `PipewireLinkError` around `PipewireBridge.start`, and this failure is earlier. The slot is released; doctor still reports `call.busy` false.
- **Simulator as a handset.** `simulator/scripts/validate-ws.mjs` and the portal Simulator tab (`#start-btn`, `#tone-mode`) need that same handshake, then a mic or a tone. `npm run validate` is not a pass on this stand-in.
- **OpenClaw Talk.** Default `webrtc-ui` needs the dedicated Chromium profile, `DISPLAY`, and CDP `127.0.0.1:9222` (`hub/TALK_WEBRTC.md`). This stand-in forces `GSM2COMPUTER_OPENCLAW_TALK=off` so a missing browser fails the doctor instead of hanging a CDP probe. Do not flip it on to "make Talk pass."
- **Bluetooth and in-call speaker routing** (`btnInCallSpeaker`). Device audio policy; see `docs/VOICE_CALL_SILENCING_INVESTIGATION.md`. The emulator has no Bluetooth headset path to check.
- **Root-only phone features.** `RootShell` runs `su`. On this emulator the Settings log records that open failing. Do not grant yourself a root image to make that line pass. The rooted moto g84 is the surface.
- **PR #23's Calls tab.** [PR #23 — Show the phone call log on the bridge Calls tab](https://github.com/safwatly/gsm2computer/pull/23) is open and is not in this tree. Seeding `content://call_log/calls` does not change `tvCallsEmpty`. The screen here is `CallLogStore`.
- **Radio SMS out.** `SmsOutboxPoller` calls `SmsManager.sendTextMessage` before it acks. Inbound `adb emu sms send` does reach the hub (see Android emulator). The curl journey acks `sent` the way the phone does after `sendTextMessage` returns. Neither proves a message left a SIM.
- **Tailscale URL and production bind.** Default hub URL `http://hub.mining-ling.ts.net:8787` and host `100.101.181.110` are the operator's machine, not this VM. The emulator cannot resolve that name. Point it at `http://10.0.2.2:<port>`.
- **Switchboard audio.** `GET /switchboard/state` shells out to `pw-link`. Without it the response is HTTP 500 `{"ok": false, "error": "internal error"}`, not a soft `ok: false` body. The portal Switchboard page then paints the local mock (`Roster local`), and a preset click looks applied. SMS bodies `STATUS` and `MODE <preset>` are stored and then hit the same `pw-link` crash, so `POST /sms` returns that 500 even though the thread row exists. Trust the messages list, not the status code, for those bodies. Do not use them for the SMS journey above.
