import { getThreads } from "./api.js";
import { faviconDataUrl, formatDocumentTitle } from "./thread-window.js";

export const deskState = {
  unread: {},
};

const VIEWER_KEY = "gsm-portal-viewer";
let memoryViewer = "";

export function unreadTotal(unread = deskState.unread) {
  return Object.values(unread || {}).reduce((sum, n) => sum + (Number(n) || 0), 0);
}

function newViewerId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `v-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function safeStorage() {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

export function portalViewer(storage) {
  const store = storage === undefined ? safeStorage() : storage;
  if (!store) {
    if (!memoryViewer) memoryViewer = newViewerId();
    return memoryViewer;
  }
  try {
    let id = store.getItem(VIEWER_KEY);
    if (!id || !/^[-A-Za-z0-9_]{1,80}$/.test(id)) {
      id = newViewerId();
      store.setItem(VIEWER_KEY, id);
    }
    return id;
  } catch {
    if (!memoryViewer) memoryViewer = newViewerId();
    return memoryViewer;
  }
}

export function replaceUnreadFromThreads(threads) {
  const next = {};
  for (const thread of threads || []) {
    const n = Number(thread.unread) || 0;
    if (n > 0 && thread.peer) next[thread.peer] = n;
  }
  deskState.unread = next;
}

export async function syncUnreadFromServer() {
  const threads = await getThreads(portalViewer());
  replaceUnreadFromThreads(threads);
  paintUnreadBadge();
  return threads;
}

export function bumpUnread(peer) {
  if (!peer) return;
  deskState.unread[peer] = (deskState.unread[peer] || 0) + 1;
}

export function clearUnread(peer) {
  if (!peer) return;
  delete deskState.unread[peer];
}

export function paintUnreadBadge() {
  const total = unreadTotal();
  if (typeof document === "undefined") return total;
  const el = document.getElementById("unread-badge");
  if (el) {
    el.hidden = total === 0;
    el.textContent = String(total);
  }
  document.querySelectorAll("[data-peer]").forEach((btn) => {
    const peer = btn.getAttribute("data-peer");
    const n = deskState.unread[peer] || 0;
    let mark = btn.querySelector(".thread-unread");
    if (n <= 0) {
      mark?.remove();
      return;
    }
    if (!mark) {
      mark = document.createElement("span");
      mark.className = "thread-unread";
      btn.querySelector(".row-head")?.appendChild(mark);
    }
    mark.textContent = String(n);
  });
  applyUnreadChrome(total);
  return total;
}

function applyUnreadChrome(total) {
  const base = document.documentElement.dataset.baseTitle || document.title || "GSM Hub";
  if (!document.documentElement.dataset.baseTitle) {
    document.documentElement.dataset.baseTitle = String(base).replace(/^\(\d+\)\s+/, "") || "GSM Hub";
  }
  document.title = formatDocumentTitle(total, document.documentElement.dataset.baseTitle);
  const link = document.querySelector('link[rel="icon"]');
  if (!link) return;
  if (!link.dataset.baseHref) link.dataset.baseHref = link.getAttribute("href") || "";
  const badge = faviconDataUrl(total);
  link.setAttribute("href", badge || link.dataset.baseHref);
}
