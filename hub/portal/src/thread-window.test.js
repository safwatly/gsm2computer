import assert from "node:assert/strict";
import test from "node:test";
import {
  dividerIndex,
  faviconDataUrl,
  formatDocumentTitle,
  isPinnedToBottom,
  jumpLatestLabel,
  mergeMessages,
  pageFromArray,
  restoreScrollAfterPrepend,
  shouldLoadOlder,
  shouldMarkRead,
  unreadDividerLabel,
} from "./thread-window.js";

const thread = [
  { id: "m0", ts: "2026-01-01T00:00:00Z", body: "0" },
  { id: "m1", ts: "2026-01-02T00:00:00Z", body: "1" },
  { id: "m2", ts: "2026-01-03T00:00:00Z", body: "2" },
  { id: "m3", ts: "2026-01-04T00:00:00Z", body: "3" },
  { id: "m4", ts: "2026-01-05T00:00:00Z", body: "4" },
];

test("pageFromArray returns the newest window and walks older", () => {
  const page = pageFromArray(thread, { limit: 2 });
  assert.deepEqual(page.messages.map((m) => m.id), ["m3", "m4"]);
  assert.equal(page.hasOlder, true);
  assert.equal(page.hasNewer, false);
  const older = pageFromArray(thread, { limit: 2, before: "2026-01-04T00:00:00Z", beforeId: "m3" });
  assert.deepEqual(older.messages.map((m) => m.id), ["m1", "m2"]);
  assert.equal(older.hasOlder, true);
  assert.equal(older.hasNewer, true);
});

test("pageFromArray since cursor excludes the cursor message", () => {
  const page = pageFromArray(thread, { limit: 10, since: "2026-01-03T00:00:00Z", sinceId: "m2" });
  assert.deepEqual(page.messages.map((m) => m.id), ["m3", "m4"]);
  assert.equal(page.hasNewer, false);
});

test("mergeMessages updates by id and keeps chronological order", () => {
  const merged = mergeMessages(
    [{ id: "1", ts: "2026-01-02T00:00:00Z", status: "queued", body: "a" }],
    [
      { id: "2", ts: "2026-01-01T00:00:00Z", status: "received", body: "b" },
      { id: "1", ts: "2026-01-02T00:00:00Z", status: "sent", body: "a" },
    ],
  );
  assert.deepEqual(
    merged.map((m) => [m.id, m.status]),
    [
      ["2", "received"],
      ["1", "sent"],
    ],
  );
});

test("divider sits on the first message after the read cursor", () => {
  assert.equal(dividerIndex(thread, { ts: "2026-01-01T00:00:00Z", id: "m0" }), 1);
  assert.equal(dividerIndex(thread, null), 0);
  assert.equal(dividerIndex(thread, { ts: "2026-01-05T00:00:00Z", id: "m4" }), -1);
  assert.equal(dividerIndex(thread, { ts: "2025-12-01T00:00:00Z", id: "z" }), 0);
  assert.equal(unreadDividerLabel(1), "1 new message");
  assert.equal(unreadDividerLabel(4), "4 new messages");
  assert.equal(jumpLatestLabel(0), "Latest");
  assert.equal(jumpLatestLabel(2), "2 new messages");
});

test("scroll helpers pin to the bottom and keep prepended history in place", () => {
  assert.equal(isPinnedToBottom({ scrollHeight: 500, scrollTop: 400, clientHeight: 100 }), true);
  assert.equal(isPinnedToBottom({ scrollHeight: 500, scrollTop: 300, clientHeight: 100 }), false);
  assert.equal(shouldLoadOlder({ scrollTop: 12 }), true);
  assert.equal(shouldLoadOlder({ scrollTop: 200 }), false);
  const el = { scrollHeight: 800, scrollTop: 0 };
  restoreScrollAfterPrepend(el, 500, 20);
  assert.equal(el.scrollTop, 320);
});

test("a hidden tab or a scrolled-up thread does not mark read", () => {
  assert.equal(shouldMarkRead({ hidden: false, pinned: true, hasMessages: true }), true);
  assert.equal(shouldMarkRead({ hidden: true, pinned: true, hasMessages: true }), false);
  assert.equal(shouldMarkRead({ hidden: false, pinned: false, hasMessages: true }), false);
  assert.equal(shouldMarkRead({ hidden: false, pinned: true, hasMessages: false }), false);
});

test("title and favicon carry the unread count", () => {
  assert.equal(formatDocumentTitle(0), "GSM Hub");
  assert.equal(formatDocumentTitle(4), "(4) GSM Hub");
  assert.equal(faviconDataUrl(0), null);
  assert.match(faviconDataUrl(2), /data:image\/svg\+xml/);
  assert.match(decodeURIComponent(faviconDataUrl(2)), />2</);
  assert.match(decodeURIComponent(faviconDataUrl(120)), />99\+</);
});
