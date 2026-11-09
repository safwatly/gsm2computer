# Bridge path walk

The Android bridge (`com.gsm2computer.bridge`) has one connection routine, `HubConnectionRoutine`. The first connect and a mid-call reconnect both call it with the same ordered list. There is no second fallback function.

Default order, when the phone has no saved list:

1. `wss://gsm.ta3leam.com/`
2. `ws://100.113.222.47:8787/`

Each attempt is `POST {origin}/token` then the WebSocket on that same origin. Both requests send `X-Gsm-Device-Token` from the device config. After `session.updated`, the bridge keeps `session.id` and sends it as `X-Gsm-Call-Session` on the next attempt, so the hub can relink instead of admitting a second call. A 409 without that session id stops the walk. A 409 with it waits and tries the list again.

`session.updated` includes `session.entrance` and `session.id`. In grokbot mode it also sets `talk` to `grokbot`. G2 binary uplink frames are accepted on the public socket. That half is `hub.tests.test_reconcile_interactions`.

## How to tell which path a call used

All of these agree while the socket is up, and the stored call row keeps the entrance after hangup:

| Where | Field |
|---|---|
| Bridge call log (Calls tab, and the `call_log` prefs) | `entrance` (`public` or `tailscale`) and `endpoint` (the URL the routine opened) |
| In-app log | `call path endpoint=… entrance=… session=…` |
| `session.updated` | `session.entrance`, `session.id` |
| `GET /health` | `call.path` and `call.entrance`. `call.route` is still the WebSocket route |
| `POST /calls` | `path`, and only when `session_id` is a socket this process accepted |

The device token is not written into any of those.

## Driving it with this skill

```bash
.cursor/skills/verify-gsm2computer/scripts/drive-bridge-paths.sh
```

The script starts an isolated hub (ports 18987 and 18990 unless you override them), then runs `HubPathReleaseTest`. That test uses `HubConnectionRoutine` and `HubCallDialer` against the live hub. It fails unless all of these pass:

- one call on the public socket only, `/health` `call.path=public`, `POST /calls` stores `path=public`
- one call on the Tailscale socket only, the same checks for `tailscale`
- the ordered list `[closed port, tailscale]` opens the second endpoint
- a dropped public socket relinks on Tailscale with the same `session.id`, and `/health` then says `tailscale`

Evidence: `/opt/cursor/artifacts/verify-gsm2computer/bridge-paths.json`. It must contain `"ok": true` and a non-empty `public`, `tailscale`, `failover`, and `relink`. It must not contain the device token.

Unit tests of ordering, failover, backoff, and the 409 stop live in `app/src/test` and run under `./gradlew test` without a hub. `HubPathReleaseTest` skips there unless `GSM2COMPUTER_BRIDGE_PATH_RELEASE=1`. A skip does not write the evidence file, and this script treats a missing or not-ok file as failure.

## Gotchas

- Config keys: `hub_endpoints` (one origin per line or comma) and `device_token`. When `hub_endpoints` is absent the walk is the two defaults, including a build 344 phone that only saved `hub_control_url`. That saved URL is not the list. Provision the list and the token with `scripts/set-bridge-entrances.sh` (root drop file, applied on boot and package replace). A missing token on an entrance is logged and that attempt is retryable, so the walk continues. The token is never logged.
- Reconnect walks the list from the start. It does not pin the entrance that happened to work last time.
- This VM does not reach `62.83.35.197` or the phone. The script's "public" socket is the hub's loopback public listener, which is what Caddy proxies on cup.
