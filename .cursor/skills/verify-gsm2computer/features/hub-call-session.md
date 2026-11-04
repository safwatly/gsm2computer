# Hub call session

One live GSM (or simulator) call occupies the hub slot. The phone opens a WebSocket, waits for `session.updated`, and streams audio. A second caller gets HTTP 409 and must not tear the first call down (ADR 0004).

## Sub-features

- `POST /token` returns `{value, model, expires_at}`. The value is the literal `hub-token`. The phone fetches this before the socket.
- WebSocket upgrade on `/` (phone path) or `/loopback` (echo, no OpenClaw). First server text frame is `session.updated` with `session.id`.
- Uplink frames are `input_audio_buffer.append`. Loopback echoes `response.output_audio.delta`. The simulator omits `format` and `rate` and stays 8 kHz μ-law.
- `GET /health` field `call` is the slot snapshot: `busy`, `path`, `session_id`, `age_s`, `link_gap_open`, and the rest of `CallSlot.snapshot`.
- A second upgrade while `call.busy` is true returns `HTTP/1.1 409` and does not replace the bridge.
- `POST /admin/call/release` is the emergency release. It refuses a live call with fresh traffic unless `?force=1`.

## How to get to it (user POV)

1. Dial the gateway number. The phone answers because it is the default dialer, then opens the hub WebSocket.
2. Talk. Caller audio is the uplink; hub audio (Talk, or the loopback echo) comes back as downlink.
3. Hang up. The socket closes `1000`. The slot goes idle on `/health`.
4. If a call is already up, another dial is rejected. The first call stays up.

The portal Simulator tab is the stand-in handset when the phone is not on the desk: set the hub URL, choose Loopback, optionally enable the test tone, press Start call, wait until the log shows `session.updated`, press End call.

## Driving it with the simulator WebSocket check

The repo's headless client is `simulator/scripts/validate-ws.mjs`. It checks `/health`, `POST /token`, the WebSocket, `session.updated`, then sends two seconds of 440 Hz μ-law.

```bash
# shellcheck disable=SC1091
source "$GSM2COMPUTER_VERIFY_HOME/instance.env"
cd simulator
npm install
node scripts/validate-ws.mjs "$GSM2COMPUTER_HUB_BASE" --loopback
```

Pass is the script printing `OK — hub path validated`, then `GET /health` with `call.busy` false again after the socket closes.

While that socket is open, a second client must get 409 and the first `session.id` must still be the one in `/health`. Close the first socket with code 1000 before you stop the hub.

Unit tests cover the slot without a socket. They are not this journey. From the repo root (`hub/` is not a package; discovery puts it on `sys.path`, matching CI):

```bash
python3 -m unittest discover -s hub -p 'test_call_*.py' -v
python3 -m unittest discover -s hub -p 'test_ws_backpressure.py' -v
```

## Gotchas

- Loopback never reaches `session.updated` on a machine without PipeWire. The socket is accepted (`101`), then `set_switchboard_mode("loopback")` runs `pw-link` from `parse_switchboard_links` and raises `FileNotFoundError: [Errno 2] No such file or directory: 'pw-link'`. The hub logs `Unhandled exception in client_connected_cb` and `WebSocket disconnected`. `pipewire_handshake_failed` is only sent when `PipewireBridge.start` raises `RuntimeError` or `PipewireLinkError`; this crash is earlier, so the client does not get that JSON error. The slot is released afterwards (`call.busy` false). `validate-ws.mjs` treats a close before `session.updated` as `closed early`. Do not score a unit-test pass as a completed call. A failed attempt still creates a directory under `GSM2COMPUTER_CALL_RECORD_DIR` (`…/calls/<timestamp>-loopback`).
- Default `GSM2COMPUTER_OPENCLAW_TALK` is `webrtc-ui`. The stand-in launch sets `off` so the handshake does not also require Chromium. Turning it back on does not make Talk available; missing CDP fails the handshake on purpose (ADR 0001). There is no fallback to `relay`.
- `GSM2COMPUTER_AUTO_MODE` defaults to `openclaw`, which runs `switchboard.sh` on a non-loopback call. The stand-in sets it empty.
- One slot per process. Two hubs side by side need two ports and two data dirs. They do not share a slot.
- `validate-ws.mjs` defaults to `http://hub.mining-ling.ts.net:8787` when you forget the URL. Pass `$GSM2COMPUTER_HUB_BASE`.
- Simulator dev UI (`cd simulator && npm run dev`, port 5173) proxies `POST /token` through `/proxy/token?hub=` because the browser would be cross-origin. The WebSocket is direct to the hub. Mic permission is required unless the tone checkbox `#tone-mode` is on. Buttons: `#start-btn`, `#end-btn`. Status text is `#status`.
