# Bridge path walk

PR #28 (not merged) teaches the Android bridge one connection routine. This hub has to answer that routine on both entrances. The bridge code is not in this branch. The hub-side drive speaks the same bytes.

Each attempt is `POST {origin}/token`, then the WebSocket on that same origin.

- Both requests send `X-Gsm-Device-Token`.
- The WebSocket also sends `Authorization: Bearer <the value minted by POST /token>`. That bearer is the ephemeral `hub-token`. It does not replace the device token.
- After `session.updated`, a reconnect sends `X-Gsm-Call-Session: <session.id>`.
- A 409 while the call is up, with or without that header, does not supersede the live call. The bridge treats a 409 that it sent with a session id as "try the list again later", and a 409 it sent without a session id as "stop".
- `POST /calls` with `session_id` stores `path` from the socket this process accepted. A client-supplied path is ignored.

`session.updated` includes `session.entrance` and `session.id`. In grokbot mode it also sets `talk` to `grokbot`. G2 binary uplink frames are accepted on the public socket.

## How this skill drives it

```bash
.cursor/skills/verify-gsm2computer/scripts/drive-bridge-paths.sh
```

Evidence: `/opt/cursor/artifacts/verify-gsm2computer/bridge-paths.json` with `"ok": true`.

The Android `HubPathReleaseTest` in PR #28 is the phone-side twin. It is not on this branch. Run it from that branch against a hub whose `hub/` is this tree when both are being cut over together.
