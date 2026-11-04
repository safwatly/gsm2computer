# Android Calls and Dialer

The bridge app (`com.gsm2computer.bridge`, launcher `MainActivity`) is a three-tab phone UI. Calls and Dialer are on the device. The portal Calls page is a different list; see [portal-call-log.md](portal-call-log.md).

[PR #23 — Show the phone call log on the bridge Calls tab](https://github.com/safwatly/gsm2computer/pull/23) is open and is not in this tree. The screen below is the bridge's own log (`CallLogStore`), not the system `CallLog` that PR adds.

## Sub-features

- Bottom bar: Dialer (`tabBtnDialer` / `tabLabelDialer`), Calls (`tabBtnCalls` / `tabLabelCalls`), Settings (`tabBtnSettings`). `GatewayHost.switchTab` takes `dialer`, `calls`, or `settings`.
- Dialer (`fragment_dialer.xml`): number line `tvDialNumber` (hint `Enter number`); keys `btnDial0`–`btnDial9`, `btnDialStar`, `btnDialHash`, `btnDialPlus`; backspace `btnBackspace` (long-press clears); call button `btnCall` (content description `Call`).
- Calls (`fragment_calls.xml`): incoming filter `btnFilterIn` (content description `Incoming`), outgoing `btnFilterOut` (`Outgoing`), clear `btnCallLogClear` (`Clear call log`), list `callLogContainer` inside `callLogScroll`, empty text `tvCallsEmpty` (`No calls yet`).
- Tapping a logged number returns to the dialer with that number. Clear asks for confirmation, then wipes `CallLogStore`.
- An empty dial string toasts `Enter a number to call`. A non-empty string opens the in-call overlay and, if the gateway service is running, sends `GatewayService.ACTION_DIAL`.

## How to get to it (user POV)

1. Open GSM2Computer on the phone (it should be the default phone app).
2. Dialer is the first tab. Tap digits. The number replaces the hint. Tap the green call button.
3. Switch to Calls. Incoming is selected first. Outgoing is the middle icon. The list is calls the bridge recorded (direction, number, time `dd/MM HH:mm`, duration), or `No calls yet`.
4. Tap a row to put that number on the dialer. The trash icon clears the log after you confirm.

## Driving it with the emulator

JVM tests do not open these tabs. The drive that does is `adb` on AVD `gsm2computer` (Pixel 6, 1080×2400, API 34). Boot and the permission grants are in [SKILL.md](../SKILL.md) under Android emulator. There is no Espresso test to copy.

```bash
./gradlew test --no-daemon
./gradlew connectedDebugAndroidTest --no-daemon
```

`test` is JVM unit tests (`app/src/test`). They cover codecs, `HubEndpoints` URL shape, call-trace helpers, and the single-call policy. They do not instantiate `CallsFragment` or `DialerFragment`, and there is no `CallLogStore` test.

`connectedDebugAndroidTest` runs `SmokeInstrumentationTest`: package name `com.gsm2computer.bridge`, and the default hub URL `http://hub.mining-ling.ts.net:8787`. It does not open a tab. On 2026-11-04 it passed 2 tests on `gsm2computer(AVD) - 14`, then uninstalled the package.

After `adb shell am start -W -n com.gsm2computer.bridge/.MainActivity`, dump the UI and tap. Do not send `KEYCODE_BACK` first; that returns to the launcher.

Proven on that image:

- Dialer showed `Enter number`, then `5551212` after the digit taps.
- Calls showed `No calls yet`.
- A `content://call_log/calls` insert of `+15551212`, type incoming, duration 42, was queryable and did not change the Calls tab.

## Gotchas

- A stock cloud VM has no Android SDK. `./gradlew test` downloads Gradle 8.5, then stops: `SDK location not found`. Run `scripts/cloud-agent-android-sdk.sh` (or boot from an environment snapshot that already has `/opt/android-sdk`). `local.properties` is gitignored.
- `/dev/kvm` is group-writable only. The shell that ran the setup script is not in group `kvm` yet. `scripts/emulator.sh` uses `sg kvm`.
- PR #23 is not merged. A seeded system call log is not this screen. Do not file a failure because `No calls yet` remains, and do not claim the PR #23 list was shown.
- `CallLogStore` updates when the bridge handles a call, which requires the GSM stack. Posting `POST /calls` to the hub does not change this screen. The in-memory cache ignores a prefs file written under a live process.
- The in-call Answer / Reject / End controls are on the overlay in `activity_main.xml` (`btnInCallAnswer`, `btnInCallReject`, `btnInCallEnd`), not on the Calls tab. Driving them needs a ringing GSM call. The green call button on the emulator is not that.
- `connectedDebugAndroidTest` removes the app when it finishes. Reinstall before another screenshot pass. Grant `RECORD_AUDIO` before launch or `GatewayService` crashes starting its microphone foreground service.
