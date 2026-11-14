/** Transcript window helpers. Messages themselves are not cached. */

export const MESSAGE_PAGE_SIZE = 50;

export function compareMessages(a, b) {
  const ts = String(a.ts || "").localeCompare(String(b.ts || ""));
  if (ts !== 0) return ts;
  return String(a.id || "").localeCompare(String(b.id || ""));
}

export function isStrictlyAfter(message, ts, id) {
  if (!ts) return true;
  if (String(message.ts || "") !== String(ts)) return String(message.ts || "") > String(ts);
  if (!id) return false;
  return String(message.id || "") > String(id);
}

export function isStrictlyBefore(message, ts, id) {
  if (!ts) return false;
  if (String(message.ts || "") !== String(ts)) return String(message.ts || "") < String(ts);
  if (!id) return false;
  return String(message.id || "") < String(id);
}

export function isAfterCursor(message, boundary) {
  if (!boundary || !boundary.ts) return true;
  return isStrictlyAfter(message, boundary.ts, boundary.id || "");
}

export function mergeMessages(existing, incoming) {
  const byId = new Map();
  for (const message of existing || []) {
    if (message && message.id) byId.set(message.id, message);
  }
  for (const message of incoming || []) {
    if (!message || !message.id) continue;
    byId.set(message.id, { ...byId.get(message.id), ...message });
  }
  return [...byId.values()].sort(compareMessages);
}

export function pageFromArray(messages, params = {}) {
  const sorted = [...(messages || [])].sort(compareMessages);
  const limit = params.limit ? Number(params.limit) : sorted.length;
  const size = Number.isFinite(limit) && limit > 0 ? limit : sorted.length;
  if (params.before) {
    const older = sorted.filter((message) => isStrictlyBefore(message, params.before, params.beforeId || ""));
    return {
      messages: older.slice(-size),
      hasOlder: older.length > size,
      hasNewer: true,
    };
  }
  if (params.since) {
    const newer = sorted.filter((message) => isStrictlyAfter(message, params.since, params.sinceId || ""));
    return {
      messages: newer.slice(0, size),
      hasOlder: sorted.length > newer.length,
      hasNewer: newer.length > size,
    };
  }
  return {
    messages: sorted.slice(-size),
    hasOlder: sorted.length > size,
    hasNewer: false,
  };
}

export function normalizeMessagePage(data, params = {}) {
  if (Array.isArray(data)) return pageFromArray(data, params);
  if (data && Array.isArray(data.messages)) {
    return {
      messages: data.messages,
      hasOlder: Boolean(data.hasOlder),
      hasNewer: Boolean(data.hasNewer),
    };
  }
  return { messages: [], hasOlder: false, hasNewer: false };
}

export function dividerIndex(messages, boundary) {
  if (!Array.isArray(messages) || messages.length === 0) return -1;
  return messages.findIndex((message) => isAfterCursor(message, boundary));
}

export function unreadDividerLabel(count) {
  const n = Number(count) || 0;
  if (n <= 0) return "";
  if (n === 1) return "1 new message";
  return `${n} new messages`;
}

export function jumpLatestLabel(pending) {
  const n = Number(pending) || 0;
  if (n <= 0) return "Latest";
  return unreadDividerLabel(n);
}

export function isPinnedToBottom(scroller, threshold = 48) {
  if (!scroller) return true;
  const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  return distance <= threshold;
}

export function shouldLoadOlder(scroller, threshold = 80) {
  if (!scroller) return false;
  return scroller.scrollTop <= threshold;
}

export function restoreScrollAfterPrepend(scroller, prevHeight, prevTop) {
  if (!scroller) return;
  const delta = scroller.scrollHeight - prevHeight;
  scroller.scrollTop = prevTop + delta;
}

export function shouldMarkRead({ hidden, pinned, hasMessages } = {}) {
  return Boolean(!hidden && pinned && hasMessages);
}

export function formatDocumentTitle(count, base = "GSM Hub") {
  const n = Number(count) || 0;
  if (n <= 0) return base;
  return `(${n}) ${base}`;
}

export function faviconDataUrl(count) {
  const n = Number(count) || 0;
  if (n <= 0) return null;
  const label = n > 99 ? "99+" : String(n);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#1a120c"/><circle cx="46" cy="18" r="16" fill="#c4a35a"/><text x="46" y="23" text-anchor="middle" font-size="16" font-family="sans-serif" font-weight="700" fill="#1a120c">${label}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
