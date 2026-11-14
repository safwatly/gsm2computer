# Portal messaging window

How the hub SMS portal compares with WhatsApp Web, Google Messages for web, Signal Desktop, Telegram Web, and iMessage, what was going wrong, and what the API does now.

## What those apps do

They all treat a thread as a window, not a document.

- The conversation opens on the newest messages, pinned to the bottom. The scrollbar tracks that window, not the user's entire history.
- Older messages load when the reader scrolls up. The viewport stays on the message they were looking at (scroll anchoring). Telegram calls the history walk `messages.getHistory` with an offset; the others do the same thing with a cursor.
- A new message appends. If the reader is already at the bottom, the view stays pinned. If they have scrolled up, the view does not jump, and a control offers "jump to latest" (often with a count).
- Unread is per reader. Fetching history does not mark a chat read. Telegram splits `messages.getHistory` from `messages.readHistory`. WhatsApp and Google Messages mark a chat read when that person has it open and can see the latest, and a business or paired API read does not clear the human's badge. Signal keeps read state per device until a receipt syncs. iMessage syncs read state across the owner's devices, not to anyone who merely listed the transcript.
- The unread badge sits on the conversation. A divider ("N new messages") marks the first unread in the transcript. The document title and favicon carry the total. Live updates arrive on a socket (or a `since` cursor after a reconnect), not by downloading the thread again.

## What this portal was doing

| | Mature apps | Portal before this change |
|---|---|---|
| First paint | Newest page, pinned to the bottom | Every row for that peer, oldest first |
| Scrollbar | The open window | The whole thread, because the page itself scrolled |
| Open position | Latest message | Top of the page (oldest message) |
| Older history | Cursor page on scroll-up | Already in the DOM |
| Unread | Server cursor per reader; listing does not advance it | In-tab counter, cleared on every paint of the open thread |
| Live update | Append one message | SSE event, then `GET` the entire thread and replace the DOM |

Nothing in the portal stored messages in `localStorage` or IndexedDB. The only copy was the module array `state.messages`, replaced on every open and every SSE event. The service worker is network-first and skips `/portal/api/`, so it does not cache the transcript either.

## Root causes

1. **The whole thread loads at once.** `PortalStore.list_messages` selects every row for the peer (`ORDER BY ts ASC`) and `GET /portal/api/messages?peer=` returned that array with no limit. `getMessages` put the array in memory and the renderer dumped it into the DOM. There is no client cache of the transcript.

2. **The thread opens at the oldest message.** `scrollThreadToLatest` set `scrollTop = scrollHeight` on `#messages`. That element had `overflow: auto` and `flex: 1`, but its parent `.thread` only had `min-height`, so it grew with the transcript and `#messages` never became the scroller. `main` (and then the document) scrolled, and a fresh page starts at the top. Every SSE event also replaced `innerHTML`, which resets that scroll.

3. **Unread never survived.** `list_threads` hardcoded `"unread": 0`. There was no read cursor in SQLite. `deskState.unread` lived in the tab: `bumpUnread` ran only when an SSE event arrived while this tab was open, the notification helper agreed, and the thread was not the one on screen. `renderMessaging` called `clearUnread` as soon as a peer was selected, including the thread the portal auto-opens. A bot `GET /portal/api/messages` did not mark anything read, because there was nothing to mark. After two days away, a new page load started from an empty counter, so the owner saw no badge. An API read still must not clear the human: the fix is a cursor per viewer, and listing messages does not move it.

4. **Live arrival re-fetched everything.** Delivery was already SSE (`GET /portal/api/events`, events `message`, `outbox`, `call`), not a poll. `handleMessagingEvent` responded to `message` and `outbox` by calling `renderMessaging`, which downloaded and repainted the full thread.

## What changed

- The portal asks for `limit=50` (the newest page). Scroll-up requests `before` and `before_id`. A reconnect asks for `since` and `since_id` and appends. The message pane is the scroller and opens at the bottom. Prepending older rows restores `scrollTop` by the height that was inserted.
- Each browser keeps a viewer id in `localStorage` under `gsm-portal-viewer` (not the messages). `GET /portal/api/threads?viewer=` returns that viewer's inbound unread count. `POST /portal/api/messages/read` moves only that viewer's cursor, and only forward. The portal posts it when the thread is visible and pinned to the latest message. A hidden tab, a reader scrolled up, and any client that only `GET`s messages leave the cursor alone.
- The open thread shows an "N new messages" divider at the first message after the cursor captured when the thread was opened. The divider stays for that visit after the badge clears. Scrolling up reveals a jump-to-latest button; messages that arrive while scrolled up are counted on that button. The Messaging badge, `document.title` (`(N) GSM Hub`), and the favicon use the sum of per-conversation unread.
- OS notifications stay optional. The badge does not depend on them.

## API compatibility

`GET /portal/api/messages?peer=` with no `before`, `since`, or `limit` is still a JSON array of the whole thread, oldest first. That is the contract bots already use. It does not write a read cursor.

Adding `limit`, and optionally `before` / `before_id` or `since` / `since_id`, returns one page:

```json
{ "messages": [], "hasOlder": false, "hasNewer": false }
```

`messages` is oldest-first inside the page. `limit` is 1..200 and defaults to 50 when a cursor is set and `limit` is omitted. `before` and `since` together are 400.

`GET /portal/api/threads` with no `viewer` still returns `unread: 0` and does not add `readTs`. `?viewer=` (1..80 of `A-Za-z0-9_-`) adds `readTs` and `readId` (`null` until that viewer has marked the thread) and a real inbound count. Outbound rows are not unread.

`POST /portal/api/messages/read` with `{ "viewer", "peer", "ts", "id" }` advances that viewer only.
