# Hub portal changelog

## 0.1.2 — Transcript window and per-viewer unread

- A thread opens on the newest page (50 messages), pinned to the bottom of `#messages`. Older pages load on scroll-up with `before` / `before_id`.
- `GET /portal/api/messages?peer=` with no window params is still the full JSON array. Bots that only list messages do not move a read cursor.
- Unread is a per-viewer cursor (`POST /portal/api/messages/read`). The thread list badge, “N new messages” divider, jump-to-latest control, document title, and favicon use that count.
- Live `message` events append or update one row. A dropped SSE connection catches up with `since` / `since_id`.

## 0.1.1 — Rich cockpit + SMS desk alerts + open latest

Operator PWA at `/portal/` with Overview, Messaging, Switchboard, and Simulator. Live updates use EventSource `GET /portal/api/events` (`connectEvents` in `src/api.js`).

### Desk alerts

- **Secure context (HTTPS / localhost):** `Enable desk alerts` asks for Notification permission once. New inbound SMS (and outbound-ack / `sent`) shows an OS notification via the service worker when possible, else `new Notification`.
- **Insecure context (typical Tailscale MagicDNS `http://hub.mining-ling.ts.net:8787/portal/`):** the Notification API is unavailable ([ADR 0007](../../docs/adr/0007-hub-machine-name-and-https.md)). The desk shows an in-app toast and an unread badge on Messaging. It does not pretend Web Push works over HTTP.
- Background PWA alerts need a **trusted HTTPS** origin. Service workers and Web Push are secure-context only; HTTP install will not receive OS notifications after the tab is gone. OpenClaw already owns Tailscale Serve `:443`, so the hub portal stays HTTP on `:8787` for now.

### Open latest

Navigating to Messaging with no `peer` selected opens the thread with the newest `lastAt`. The message scroller sets `scrollTop = scrollHeight` after render and after live events.
