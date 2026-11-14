import assert from "node:assert/strict";
import test from "node:test";
import { deskState, portalViewer, replaceUnreadFromThreads, unreadTotal } from "./desk-state.js";

test("portal viewer id is stable for one browser", () => {
  const mem = {
    data: new Map(),
    getItem(key) {
      return this.data.has(key) ? this.data.get(key) : null;
    },
    setItem(key, value) {
      this.data.set(key, value);
    },
  };
  const first = portalViewer(mem);
  const second = portalViewer(mem);
  assert.equal(first, second);
  assert.match(first, /^[0-9a-f-]{36}$/i);
});

test("thread unread replaces the in-memory map", () => {
  replaceUnreadFromThreads([
    { peer: "+1", unread: 2 },
    { peer: "+2", unread: 0 },
    { peer: "+3", unread: 4 },
  ]);
  assert.deepEqual(deskState.unread, { "+1": 2, "+3": 4 });
  assert.equal(unreadTotal(), 6);
  replaceUnreadFromThreads([]);
  assert.equal(unreadTotal(), 0);
});
