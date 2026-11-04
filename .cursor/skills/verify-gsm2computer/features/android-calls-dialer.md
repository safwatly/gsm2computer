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

## Driving it with Gradle

Nothing in the repo clicks these tabs.

What exists:

```bash
./gradlew test --no-daemon
./gradlew connectedAndroidTest --no-daemon
```

`test` is JVM unit tests (`app/src/test`). They cover codecs, `HubEndpoints` URL shape, call-trace helpers, and the single-call policy. They do not instantiate `CallsFragment` or `DialerFragment`, and there is no `CallLogStore` test.

`connectedAndroidTest` runs `SmokeInstrumentationTest`: package name `com.gsm2computer.bridge`, and the default hub URL `http://hub.mining-ling.ts.net:8787`. It does not open a tab. It needs a device or emulator (`adb`, SDK platform 34).

The activity to launch, on hardware that has the APK installed:

```bash
adb shell am start -n com.gsm2computer.bridge/.MainActivity
```

View ids for a human or a future instrumentation test are the ones in Sub-features. There is no Espresso test to copy.

## Gotchas

- No Android SDK on a stock cloud VM (`ANDROID_HOME` unset, no `/opt/android-sdk`). `./gradlew test` downloads Gradle 8.5 if needed, then stops before tests: `SDK location not found`. `sudo ./setup.sh` installs the SDK; that is a root setup step, not a substitute for a phone.
- No `adb`, no emulator, no moto g84. Do not describe the Calls tab as verified.
- PR #23 is not merged. Do not expect the system call log, and do not file a failure because it is absent.
- `CallLogStore` updates when the bridge handles a call, which requires the GSM stack. Posting `POST /calls` to the hub does not change this screen.
- The in-call Answer / Reject / End controls are on the overlay in `activity_main.xml` (`btnInCallAnswer`, `btnInCallReject`, `btnInCallEnd`), not on the Calls tab. Driving them needs a ringing GSM call.
