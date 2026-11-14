import { getHealth, getMessages, getThreads, markThreadRead, sendMessage } from "./api.js";
import { clearUnread, deskState, paintUnreadBadge, portalViewer, replaceUnreadFromThreads, unreadTotal } from "./desk-state.js";
import { href, navigate, replaceLocation } from "./router.js";
import { formatFixturePeer, shellHtml } from "./shell.js";
import {
  MESSAGE_PAGE_SIZE,
  dividerIndex,
  isPinnedToBottom,
  jumpLatestLabel,
  mergeMessages,
  restoreScrollAfterPrepend,
  shouldLoadOlder,
  shouldMarkRead,
  unreadDividerLabel,
} from "./thread-window.js";

export { unreadTotal };

const state = {
  peer: null,
  threads: [],
  messages: [],
  error: "",
  sending: false,
  mock: false,
  suppressAutoOpen: false,
  hasOlder: false,
  loadedPeer: null,
  stickToBottom: true,
  loadingOlder: false,
  boundary: null,
  dividerCount: 0,
  pendingBelow: 0,
  readToken: "",
  draft: "",
};

let boundApp = null;

export function pickLatestThreadPeer(threads) {
  if (!Array.isArray(threads) || threads.length === 0) return null;
  const sorted = [...threads].sort((a, b) => String(b.lastAt || "").localeCompare(String(a.lastAt || "")));
  return sorted[0].peer;
}

export function resolveMessagingPeer({ peer, suppressAutoOpen, threads }) {
  if (peer) return peer;
  if (suppressAutoOpen) return null;
  return pickLatestThreadPeer(threads);
}

export function scrollThreadToLatest(scroller) {
  if (!scroller) return;
  const run = () => {
    scroller.scrollTop = scroller.scrollHeight;
  };
  run();
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(run);
  }
}

export function parseMessagingRest(rest) {
  if (rest.startsWith("/thread/")) {
    state.peer = decodeURIComponent(rest.slice("/thread/".length));
    state.suppressAutoOpen = false;
    return;
  }
  state.peer = null;
}

export function getMessagingContext() {
  return { peer: state.peer };
}

export function resetMessagingSuppress() {
  state.suppressAutoOpen = false;
}

export function openMessagingPeer(peer) {
  if (!peer) return;
  state.suppressAutoOpen = false;
  state.peer = peer;
  navigate(`/messaging/thread/${encodeURIComponent(peer)}`);
}

function setMessagingPath() {
  if (state.peer) {
    navigate(`/messaging/thread/${encodeURIComponent(state.peer)}`);
  } else {
    navigate("/messaging");
  }
}

function applyThreads(threads) {
  state.threads = Array.isArray(threads) ? threads : [];
  replaceUnreadFromThreads(state.threads);
  if (state.readToken && state.peer && state.stickToBottom && !document.hidden) {
    clearUnread(state.peer);
    const open = state.threads.find((thread) => thread.peer === state.peer);
    if (open) open.unread = 0;
  }
}

async function refreshThreads() {
  const threads = await getThreads(portalViewer());
  applyThreads(threads);
}

async function loadLatest(peer) {
  const page = await getMessages(peer, { limit: MESSAGE_PAGE_SIZE });
  state.messages = page.messages;
  state.hasOlder = Boolean(page.hasOlder);
  state.loadedPeer = peer;
  state.stickToBottom = true;
  state.pendingBelow = 0;
  state.readToken = "";
  const thread = state.threads.find((item) => item.peer === peer);
  state.dividerCount = Number(thread?.unread) || 0;
  state.boundary = thread?.readTs ? { ts: thread.readTs, id: thread.readId || "" } : null;
}

export async function renderMessaging(app) {
  boundApp = app;
  try {
    state.error = "";
    const healthRes = await getHealth();
    state.mock = Boolean(healthRes.mock);
    await refreshThreads();
    const nextPeer = resolveMessagingPeer({
      peer: state.peer,
      suppressAutoOpen: state.suppressAutoOpen,
      threads: state.threads,
    });
    if (!state.peer && nextPeer) {
      replaceLocation(`/messaging/thread/${encodeURIComponent(nextPeer)}`);
    }
    state.peer = nextPeer;
    if (state.peer) {
      await loadLatest(state.peer);
    } else {
      state.messages = [];
      state.loadedPeer = null;
      state.hasOlder = false;
    }
  } catch (err) {
    state.error = err.message || String(err);
  }
  paint(app, { stick: true });
  await markVisibleRead();
}

function paint(app, mode = {}) {
  const area = app.querySelector("#compose textarea");
  if (area) state.draft = area.value;
  const hadFocus = Boolean(area && typeof document !== "undefined" && document.activeElement === area);
  const selection = hadFocus ? [area.selectionStart, area.selectionEnd] : null;
  app.innerHTML = `
    ${shellHtml({ page: "messaging", mock: state.mock })}
    ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
    <main>${renderMessagesLayout()}</main>
  `;
  paintUnreadBadge();
  app.querySelectorAll("[data-peer]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.suppressAutoOpen = false;
      state.peer = btn.getAttribute("data-peer");
      setMessagingPath();
    });
  });
  app.querySelector("[data-threads-back]")?.addEventListener("click", (ev) => {
    ev.preventDefault();
    state.suppressAutoOpen = true;
    state.peer = null;
    state.loadedPeer = null;
    setMessagingPath();
  });
  bindThread(app, mode);
  const next = app.querySelector("#compose textarea");
  if (hadFocus && next) {
    next.focus();
    if (selection && typeof next.setSelectionRange === "function") {
      next.setSelectionRange(selection[0], selection[1]);
    }
  }
}

function renderMessagesLayout() {
  const hasPeer = Boolean(state.peer);
  return `<div class="msg-layout${hasPeer ? " has-peer" : ""}">
    <div class="msg-list-pane">${renderThreads()}</div>
    <div class="msg-detail-pane">${hasPeer ? renderThreadInner() : `<div class="empty">Select a thread. Pixel forwarder idle until a peer is chosen.</div>`}</div>
  </div>`;
}

function renderThreads() {
  if (!state.threads.length) {
    return `<div class="empty">No SMS on the wire. Pixel forwarder idle.</div>`;
  }
  return `<ul class="list">${state.threads
    .map((t) => {
      const on = t.peer === state.peer ? " on" : "";
      const unread = deskState.unread[t.peer] || 0;
      const mark = unread ? `<span class="thread-unread">${unread}</span>` : "";
      return `
      <li>
        <button class="row${on}" data-peer="${escapeAttr(t.peer)}">
          <span class="row-head"><strong>${escapeHtml(formatFixturePeer(t.peer) || t.peer)}</strong>${mark}</span>
          <span>${escapeHtml(t.lastBody || "")}</span>
          <span class="meta">${escapeHtml(fmtTime(t.lastAt))}</span>
        </button>
      </li>`;
    })
    .join("")}</ul>`;
}

function renderThreadInner() {
  const dividerAt = state.dividerCount > 0 ? dividerIndex(state.messages, state.boundary) : -1;
  const msgs = state.messages
    .map((m, index) => {
      const divider =
        index === dividerAt
          ? `<div class="unread-divider" role="separator"><span>${escapeHtml(unreadDividerLabel(state.dividerCount))}</span></div>`
          : "";
      const side = m.direction === "out" ? "out" : "in";
      const st =
        m.status && m.status !== "received" && m.status !== "sent"
          ? `<span class="status-pill">${escapeHtml(m.status)}${m.error ? ` ${escapeHtml(m.error)}` : ""}</span>`
          : "";
      return `${divider}<div class="bubble ${side}" data-id="${escapeAttr(m.id || "")}"><span class="bubble-body">${escapeHtml(m.body || "")}</span>${st}<time>${escapeHtml(fmtTime(m.ts))}</time></div>`;
    })
    .join("");
  const peerLabel = formatFixturePeer(state.peer) || state.peer;
  const jump = jumpLatestLabel(state.pendingBelow);
  return `
    <div class="thread">
      <div class="thread-head">
        <a class="back" data-threads-back href="${href("/messaging")}">‹ Threads</a>
        <span class="thread-peer">${escapeHtml(peerLabel)}</span>
      </div>
      ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
      <div class="messages" id="messages">${msgs || `<div class="empty">No messages in this thread.</div>`}</div>
      <button type="button" class="jump-latest" id="jump-latest" hidden>${escapeHtml(jump)}</button>
      <form class="compose" id="compose">
        <textarea name="body" rows="1" placeholder="SMS" required>${escapeHtml(state.draft || "")}</textarea>
        <button type="submit" ${state.sending ? "disabled" : ""}>Send</button>
      </form>
    </div>
  `;
}

function bindThread(app, mode) {
  const scroller = app.querySelector("#messages");
  if (mode.anchor && scroller) {
    restoreScrollAfterPrepend(scroller, mode.anchor.height, mode.anchor.top);
  } else if (mode.preserveTop != null && scroller) {
    scroller.scrollTop = mode.preserveTop;
  } else if (mode.stick || state.stickToBottom) {
    scrollThreadToLatest(scroller);
  }
  paintJump(scroller);
  scroller?.addEventListener(
    "scroll",
    () => {
      const pinned = isPinnedToBottom(scroller);
      state.stickToBottom = pinned;
      if (pinned) state.pendingBelow = 0;
      paintJump(scroller);
      if (pinned) markVisibleRead();
      if (!pinned && shouldLoadOlder(scroller)) loadOlder(app);
    },
    { passive: true },
  );
  app.querySelector("#jump-latest")?.addEventListener("click", () => {
    state.stickToBottom = true;
    state.pendingBelow = 0;
    scrollThreadToLatest(scroller);
    paintJump(scroller);
    markVisibleRead();
  });
  const form = app.querySelector("#compose");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const areaEl = ev.target.querySelector("textarea");
    const btn = ev.target.querySelector("button");
    const body = areaEl.value.trim();
    if (!body || state.sending) return;
    state.sending = true;
    state.draft = "";
    if (btn) btn.disabled = true;
    try {
      const sent = await sendMessage(state.peer, body);
      const parts = Array.isArray(sent?.parts) ? sent.parts : [sent];
      const created = parts.map((part) => part?.message).filter(Boolean);
      state.sending = false;
      if (created.length) {
        state.messages = mergeMessages(state.messages, created);
        state.stickToBottom = true;
        state.pendingBelow = 0;
        paint(app, { stick: true });
        await markVisibleRead();
      } else {
        await loadLatest(state.peer);
        paint(app, { stick: true });
        await markVisibleRead();
      }
    } catch (err) {
      state.sending = false;
      state.draft = body;
      state.error = err.message || String(err);
      paint(app, { stick: state.stickToBottom });
    }
  });
  if (state.stickToBottom && state.hasOlder && scroller && scroller.scrollHeight <= scroller.clientHeight + 8) {
    loadOlder(app);
  }
}

function paintJump(scroller) {
  const btn = document.getElementById("jump-latest");
  if (!btn) return;
  const pinned = scroller ? isPinnedToBottom(scroller) : state.stickToBottom;
  btn.hidden = pinned;
  btn.textContent = jumpLatestLabel(state.pendingBelow);
}

async function loadOlder(app, chain = 0) {
  if (state.loadingOlder || !state.hasOlder || !state.messages.length || !state.peer) return;
  state.loadingOlder = true;
  const oldest = state.messages[0];
  let failed = false;
  try {
    const page = await getMessages(state.peer, {
      limit: MESSAGE_PAGE_SIZE,
      before: oldest.ts,
      beforeId: oldest.id,
    });
    const fresh = page.messages.filter((message) => !state.messages.some((existing) => existing.id === message.id));
    if (!fresh.length) {
      state.hasOlder = false;
      return;
    }
    const scroller = app.querySelector("#messages");
    const stickNow = state.stickToBottom;
    const anchor = scroller ? { height: scroller.scrollHeight, top: scroller.scrollTop } : null;
    state.messages = mergeMessages(page.messages, state.messages);
    state.hasOlder = Boolean(page.hasOlder);
    paint(app, stickNow ? { stick: true } : { anchor });
  } catch (err) {
    failed = true;
    state.error = err.message || String(err);
    paint(app, { stick: false, preserveTop: app.querySelector("#messages")?.scrollTop });
  } finally {
    state.loadingOlder = false;
  }
  if (failed || chain >= 3 || !state.hasOlder) return;
  const scroller = app.querySelector("#messages");
  if (!scroller) return;
  const short = scroller.scrollHeight <= scroller.clientHeight + 8;
  if (state.stickToBottom && short) await loadOlder(app, chain + 1);
  else if (!state.stickToBottom && shouldLoadOlder(scroller)) await loadOlder(app, chain + 1);
}

export async function markVisibleRead() {
  const newest = state.messages[state.messages.length - 1];
  if (
    !shouldMarkRead({
      hidden: typeof document !== "undefined" && document.hidden,
      pinned: state.stickToBottom,
      hasMessages: Boolean(newest && state.peer),
    })
  ) {
    return false;
  }
  const token = `${state.peer}\0${newest.ts}\0${newest.id || ""}`;
  if (state.readToken === token) return true;
  state.readToken = token;
  state.pendingBelow = 0;
  clearUnread(state.peer);
  const thread = state.threads.find((item) => item.peer === state.peer);
  if (thread) {
    thread.unread = 0;
    thread.readTs = newest.ts;
    thread.readId = newest.id || "";
  }
  paintUnreadBadge();
  paintJump(document.getElementById("messages"));
  try {
    await markThreadRead({
      viewer: portalViewer(),
      peer: state.peer,
      ts: newest.ts,
      id: newest.id || "",
    });
  } catch (err) {
    if (!state.mock) {
      state.readToken = "";
      console.warn(err);
    }
  }
  return true;
}

async function applyLiveMessage(app, event) {
  const scroller = app.querySelector("#messages");
  const pinned = scroller ? isPinnedToBottom(scroller) : state.stickToBottom;
  state.stickToBottom = pinned;
  const preserveTop = scroller ? scroller.scrollTop : 0;
  const known = state.messages.some((message) => message.id === event.id);
  state.messages = mergeMessages(state.messages, [event]);
  const seen = shouldMarkRead({
    hidden: document.hidden,
    pinned,
    hasMessages: true,
  });
  if (!known && event.direction === "in" && !seen) state.pendingBelow += 1;
  try {
    await refreshThreads();
  } catch (err) {
    console.warn(err);
  }
  paint(app, pinned ? { stick: true } : { preserveTop });
  if (seen) await markVisibleRead();
}

async function catchUp(app) {
  const scroller = app.querySelector("#messages");
  const preserveTop = scroller ? scroller.scrollTop : 0;
  const pinned = state.stickToBottom;
  try {
    await refreshThreads();
  } catch (err) {
    console.warn(err);
  }
  if (!state.peer || !state.messages.length) {
    paint(app, { stick: pinned });
    return;
  }
  let newest = state.messages[state.messages.length - 1];
  let addedInbound = 0;
  for (let pageNo = 0; pageNo < 20; pageNo += 1) {
    const page = await getMessages(state.peer, {
      limit: MESSAGE_PAGE_SIZE,
      since: newest.ts,
      sinceId: newest.id,
    });
    if (!page.messages.length) break;
    const inbound = page.messages.filter(
      (message) => message.direction === "in" && !state.messages.some((existing) => existing.id === message.id),
    );
    state.messages = mergeMessages(state.messages, page.messages);
    addedInbound += inbound.length;
    newest = state.messages[state.messages.length - 1];
    if (!page.hasNewer) break;
  }
  if (!pinned && addedInbound) state.pendingBelow += addedInbound;
  paint(app, pinned ? { stick: true } : { preserveTop });
  if (pinned) await markVisibleRead();
}

export async function handleMessagingEvent(app, type, data) {
  const target = app || boundApp;
  if (!target) return;
  if (type === "reconnect") {
    await catchUp(target);
    return;
  }
  if (type !== "message") return;
  const event = data && typeof data === "object" ? data : null;
  if (event && state.peer && event.peer === state.peer && state.loadedPeer === state.peer) {
    await applyLiveMessage(target, event);
    return;
  }
  try {
    await refreshThreads();
  } catch (err) {
    console.warn(err);
  }
  const scroller = target.querySelector("#messages");
  paint(target, state.stickToBottom ? { stick: true } : { preserveTop: scroller ? scroller.scrollTop : 0 });
}

export function bindMessagingApp(app) {
  boundApp = app;
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}
