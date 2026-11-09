# ADR 0008: Public phone entrance on cup

- **Status:** accepted
- **Date:** 2026-11-08

## Context

Call audio is a WebSocket from the bridge app to the hub. The hub listens on cup's Tailscale address (`100.113.222.47:8787`). Tailscale often hairpins through a DERP relay, and when Tailscale on the phone dies the call dies with it. The phone cannot be dialed (carrier NAT, changing address). Cup's public address `62.83.35.197` is fixed, so the phone must dial out to cup.

The handler in `hub.py` is one function (`handle_client`). Existing bridge builds send `Authorization: Bearer hub-token`, where `hub-token` is the placeholder minted by `POST /token`. That header is not a secret. No DNS name in this repo points at `62.83.35.197`. Safwat controls `ta3leam.com` on Cloudflare.

## Decision

1. **One handler, two listen sockets.** `listen_entrance` only tags the accepted socket (`public` or `tailscale`) and calls `handle_client`. There is no second request parser.
2. **One token check.** `device_auth.authorize` runs for every request. The device secret is the header `X-Gsm-Device-Token`. `Authorization: Bearer` counts only when the bearer value is itself a configured device token, so today's `Bearer hub-token` is ignored.
3. **Transition.** The public socket requires a device token. The Tailscale socket does not, until `GSM2COMPUTER_TAILSCALE_TOKEN_REQUIRED=1`. A wrong `X-Gsm-Device-Token` is rejected on both. Existing bridge builds keep working on Tailscale with no new header.
4. **Public routes.** After the token check, the public socket serves phone ingest only: WebSocket `/`, `/health`, `/token`, `/sms`, `/sms/outbox`, `/sms/outbox/{id}/ack`, and `/calls`. `/loopback`, `/e2e-test`, and `/switchboard` stay on the Tailscale socket because they retarget the audio graph. `POST /sms` on the public socket stores the message and does not run `MODE` / `STATUS` switchboard commands. Portal and `/admin` stay on Tailscale.
5. **TLS is Caddy, not hub-native TLS.** Caddy binds `62.83.35.197:443` only (not a wildcard) and obtains a Let's Encrypt certificate with TLS-ALPN-01 on that address. The ACME issuer uses the same `bind_host`. HTTP redirects and HTTP-01 stay off, and HTTP/3 is not enabled, so Caddy does not listen on port 80 or UDP 443. It reverse-proxies HTTP/1.1 to `127.0.0.1:8790`. Tailscale Serve keeps the tailnet address on `:443`. The DNS record is **DNS-only** (Cloudflare grey cloud) so the phone connects to `62.83.35.197` and TLS-ALPN reaches cup. Recommended name, not previously used by this repo: `gsm.ta3leam.com`.
6. **Entrance is not a header.** The hub does not trust `X-Forwarded-*` or any client header to decide `public` vs `tailscale`. A client that can hit `127.0.0.1:8790` is the public entrance and still needs the device token.
7. **Call path.** `/health` `call.path` and `call.entrance` are `public` or `tailscale` for the socket that is carrying the call, including after a relink onto the other entrance. `session.updated` is built from that same slot at send time. `call.route` keeps the WebSocket route. `POST /calls` stores `path` only when `session_id` matches a socket this process accepted. Otherwise `path` is null. A client-supplied path is not stored.
8. **Firewall.** `deploy/cup/gsm2computer-nft.sh` installs table `inet gsm2computer` only. It does not flush the host ruleset. The public interface default-denies. Allowed: the TLS port, SSH, Tailscale's UDP port, ICMP errors needed for path MTU, and IPv6 neighbor discovery (solicit, advert, router solicit, router advert). `tailscale0` is not filtered by this table. RustDesk `21118` and `21125-21129` are not in the allow list. `apply` arms a rollback timer before the rules take effect. `confirm` persists the rules and enables the boot unit before it cancels that timer. `revert` restores the previous table. The systemd unit has no `ExecStop` that deletes the table.

## Consequences

- Deploy hub and Caddy together. Leave `GSM2COMPUTER_PUBLIC_ENABLE` unset until the token file, DNS, and firewall are ready. See [CUP_PUBLIC_ENTRANCE.md](../CUP_PUBLIC_ENTRANCE.md).
- Step 3 (bridge client) uses one connection routine and the ordered origins `[https://gsm.ta3leam.com, http://<tailscale-host>:8787]`, with `X-Gsm-Device-Token` on every phone request. This ADR does not change the phone.
- Do not `tailscale serve` this listener. OpenClaw still owns MagicDNS `:443` on the tailnet ([ADR 0007](0007-hub-machine-name-and-https.md)). Public `:443` is Caddy on the public address, a different interface.
