# Public and Tailscale entrances

## Sub-features

- Tailscale listen socket (default `:18787` in this skill, `:8787` on cup) and public listen socket (`127.0.0.1:18790` here, `127.0.0.1:8790` on cup).
- One `handle_client`. The accept callback only records which socket accepted.
- Device token header `X-Gsm-Device-Token`.
- Public entrance requires the token. Tailscale allows a missing token until `GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=1`.
- A wrong device token is rejected on both.
- `Authorization: Bearer hub-token` is the ephemeral mint from `POST /token`. It is not a device secret. It still works on Tailscale and does not open the public entrance.
- `/health` `call.path` and `call.entrance` are `public` or `tailscale` while a call is up. `call.route` is the WebSocket route.
- Portal, `/admin/call/release`, `/switchboard`, `/loopback`, and `/e2e-test` are not reachable on the public socket, even with a valid device token. Public `POST /sms` with `MODE` stores the SMS and does not change the switchboard.
- Caddy in front of the public socket. Production uses Let's Encrypt. This skill uses Caddy `tls internal`.

## How to get to it (user POV)

The bridge opens `wss://gsm.ta3leam.com/` (after deploy) or `ws://<tailscale-host>:8787/`. The operator curls `/health`. During a call, `call.path` says which entrance the socket used.

## Driving it with this skill

```bash
.cursor/skills/verify-gsm2computer/scripts/launch-hub.sh
.cursor/skills/verify-gsm2computer/scripts/doctor.sh
python3 .cursor/skills/verify-gsm2computer/scripts/drive-entrances.py
```

`drive-entrances.py` is the journey: missing token, bad token, good token, legacy bearer, hidden portal and switchboard, public `MODE` ignored, a public `/` call, Tailscale loopback echo, a cross-entrance relink, call reports that stay unknown without a matching session, and a second hub process with Tailscale token required. It then proxies the public socket through Caddy.

## Gotchas

- Do not point the public `GSM2COMPUTER_PUBLIC_HOST` at `0.0.0.0` on cup. Caddy should be the only public listener.
- The device token file is mode `0600`. The drive script must not print it into the evidence JSON.
- Existing bridge builds do not send `X-Gsm-Device-Token`. The Tailscale socket in the default launch keeps `token_required` false for that reason. The "no token rejected" case for Tailscale is the second process (`GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=1` on `:18891`).
- `call.path` used to be the WebSocket route. It is now the entrance. The route is `call.route`.
- This VM's Caddy certificate is not the certificate cup will serve. See the skill's stand-in list.
