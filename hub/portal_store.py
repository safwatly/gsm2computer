#!/usr/bin/env python3
"""SQLite persistence for the hub messaging portal (ADR 0006)."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    peer TEXT NOT NULL,
    body TEXT NOT NULL,
    ts TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_peer_ts ON messages (peer, ts);

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    number TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_sec INTEGER NOT NULL,
    switchboard_mode TEXT,
    session_id TEXT,
    tap_summary TEXT,
    path TEXT
);
CREATE INDEX IF NOT EXISTS calls_started ON calls (started_at);

CREATE TABLE IF NOT EXISTS call_link_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT,
    kind TEXT NOT NULL,
    close_code INTEGER,
    close_reason TEXT,
    initiator TEXT,
    detail TEXT,
    entrance TEXT
);
CREATE INDEX IF NOT EXISTS call_link_events_ts ON call_link_events (ts);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    to_number TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    available_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS outbox_status ON outbox (status, created_at);
"""


RECORDING_FILENAMES = ("openclaw.mp3", "openclaw-relay.mp3", "gsm.mp3")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# A conservative modem/carrier settling interval. The Pixel's 10s poll means
# the actual gap will normally be at least this long.
OUTBOUND_SMS_GAP_SECONDS = 6

# Longer than the phone's 45s sent-intent wait. A dead process never acks.
# Expiring the lease marks timeout. It does not put the row back in pending:
# the radio may already have accepted the message.
OUTBOX_SEND_LEASE_SECONDS = 90


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_e164(raw: str) -> str:
    """Normalize to E.164 where possible; empty input stays empty."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"[\s().\-]", "", s)
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        return "+" + digits if digits else ""
    digits = re.sub(r"\D", "", s)
    if not digits:
        return raw.strip()
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def normalize_peer(raw: str) -> str:
    """Name the portal tests import. Same rules as normalize_e164."""
    return normalize_e164(raw)


def _row_message(row: sqlite3.Row) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "direction": row["direction"],
        "peer": row["peer"],
        "body": row["body"],
        "ts": row["ts"],
        "status": row["status"],
    }
    error = _optional_text(row, "error")
    if error:
        item["error"] = error
    return item


def _row_call(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "direction": row["direction"],
        "number": row["number"],
        "started_at": row["started_at"],
        "duration_sec": int(row["duration_sec"] or 0),
        "switchboard_mode": row["switchboard_mode"],
        "session_id": row["session_id"],
        "tap_summary": row["tap_summary"],
        "path": row["path"] if "path" in row.keys() else None,
    }


def _parse_tap_summary(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _safe_record_folder(record_dir: Path, name: str) -> Optional[Path]:
    label = (name or "").strip()
    if not label or label in {".", ".."} or "/" in label or "\\" in label:
        return None
    folder = record_dir / label
    if not _is_under(folder, record_dir):
        return None
    return folder


def _recording_in_dir(folder: Path, record_dir: Path) -> Optional[Path]:
    if not folder.is_dir() or not _is_under(folder, record_dir):
        return None
    for filename in RECORDING_FILENAMES:
        candidate = folder / filename
        if candidate.is_file() and _is_under(candidate, record_dir):
            return candidate
    return None


def _started_at_prefixes(started_at: str) -> list[str]:
    raw = (started_at or "").strip()
    if not raw:
        return []
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return []
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return [dt.strftime("%Y%m%dT%H%M%SZ"), dt.strftime("%Y%m%dT%H%M")]


def resolve_call_recording(call: dict[str, Any], record_dir: Path) -> Optional[Path]:
    """Find OpenClaw MP3: session_id → tap_summary.id/dir → started_at prefix."""
    root = Path(record_dir)
    if not call or not root.exists():
        return None

    names: list[str] = []
    session_id = str(call.get("session_id") or "").strip()
    if session_id:
        names.append(session_id)

    tap = _parse_tap_summary(call.get("tap_summary"))
    tap_dir_raw = ""
    if tap:
        tap_id = str(tap.get("id") or "").strip()
        if tap_id:
            names.append(tap_id)
        tap_dir_raw = str(tap.get("dir") or "").strip()

    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        folder = _safe_record_folder(root, name)
        if folder:
            found = _recording_in_dir(folder, root)
            if found:
                return found

    if tap_dir_raw:
        folder = Path(tap_dir_raw)
        found = _recording_in_dir(folder, root)
        if found:
            return found

    if root.is_dir():
        dirs = [p for p in root.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.name, reverse=True)
        for prefix in _started_at_prefixes(str(call.get("started_at") or "")):
            for folder in dirs:
                if folder.name.startswith(prefix):
                    found = _recording_in_dir(folder, root)
                    if found:
                        return found
    return None


def _optional_text(row: sqlite3.Row, key: str) -> str:
    try:
        if key not in row.keys():
            return ""
        return str(row[key] or "")
    except (IndexError, KeyError):
        return ""


def _row_outbox(row: sqlite3.Row) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "to": row["to_number"],
        "body": row["body"],
        "created_at": row["created_at"],
        "available_at": row["available_at"],
        "status": row["status"],
    }
    error = _optional_text(row, "error")
    if error:
        item["error"] = error
    return item


class PortalStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(outbox)")}
            if "available_at" not in columns:
                self._conn.execute("ALTER TABLE outbox ADD COLUMN available_at TEXT NOT NULL DEFAULT ''")
            self._conn.execute("UPDATE outbox SET available_at = created_at WHERE available_at = ''")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS outbox_destination_queue "
                "ON outbox (to_number, status, available_at, created_at)"
            )
            call_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(calls)")}
            if "path" not in call_columns:
                self._conn.execute("ALTER TABLE calls ADD COLUMN path TEXT")
            link_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(call_link_events)")}
            if "entrance" not in link_columns:
                self._conn.execute("ALTER TABLE call_link_events ADD COLUMN entrance TEXT")
            message_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(messages)")}
            if "client_id" not in message_columns:
                self._conn.execute("ALTER TABLE messages ADD COLUMN client_id TEXT")
            if "error" not in message_columns:
                self._conn.execute("ALTER TABLE messages ADD COLUMN error TEXT NOT NULL DEFAULT ''")
            if "error" not in columns:
                self._conn.execute("ALTER TABLE outbox ADD COLUMN error TEXT NOT NULL DEFAULT ''")
            if "claimed_at" not in columns:
                self._conn.execute("ALTER TABLE outbox ADD COLUMN claimed_at TEXT NOT NULL DEFAULT ''")
                self._conn.execute(
                    "UPDATE outbox SET claimed_at = ? WHERE status = 'sending' AND claimed_at = ''",
                    (utc_now(),),
                )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS messages_client_id "
                "ON messages (client_id) WHERE client_id IS NOT NULL AND client_id != ''"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add_message(
        self,
        direction: str,
        peer: str,
        body: str,
        ts: Optional[str] = None,
        status: str = "received",
        msg_id: Optional[str] = None,
    ) -> dict[str, Any]:
        peer_n = normalize_e164(peer) or peer.strip()
        item = {
            "id": msg_id or str(uuid.uuid4()),
            "direction": direction,
            "peer": peer_n,
            "body": body if body is not None else "",
            "ts": ts or utc_now(),
            "status": status,
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, direction, peer, body, ts, status) VALUES (?, ?, ?, ?, ?, ?)",
                (item["id"], item["direction"], item["peer"], item["body"], item["ts"], item["status"]),
            )
            self._conn.commit()
        return item

    def message_by_client_id(self, client_id: str) -> Optional[dict[str, Any]]:
        client = (client_id or "").strip()
        if not client:
            return None
        with self._lock:
            return self._lookup_client_id(client)

    def add_inbound_message(
        self,
        peer: str,
        body: str,
        ts: Optional[str] = None,
        status: str = "received",
        client_id: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        """Insert one inbound SMS. Returns (row, created).

        A repeat of the same [client_id] returns the stored row and created=False
        so a lost response can be retried on the other entrance without a second
        message, webhook, or switchboard command.
        """
        client = (client_id or "").strip()[:80] or None
        peer_n = normalize_e164(peer) or peer.strip()
        item = {
            "id": str(uuid.uuid4()),
            "direction": "in",
            "peer": peer_n,
            "body": body if body is not None else "",
            "ts": ts or utc_now(),
            "status": status,
        }
        with self._lock:
            if client:
                existing = self._lookup_client_id(client)
                if existing is not None:
                    return existing, False
            try:
                self._conn.execute(
                    "INSERT INTO messages (id, direction, peer, body, ts, status, client_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        item["id"],
                        item["direction"],
                        item["peer"],
                        item["body"],
                        item["ts"],
                        item["status"],
                        client,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                self._conn.rollback()
                if client:
                    existing = self._lookup_client_id(client)
                    if existing is not None:
                        return existing, False
                raise
        return item, True

    def _lookup_client_id(self, client_id: str) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT id, direction, peer, body, ts, status FROM messages WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_message(row)

    def list_threads(self) -> list[dict[str, Any]]:
        sql = """
            SELECT m.peer, m.body, m.ts, m.direction, m.status
            FROM messages m
            INNER JOIN (
                SELECT peer, MAX(ts) AS last_ts
                FROM messages
                GROUP BY peer
            ) latest ON latest.peer = m.peer AND latest.last_ts = m.ts
            ORDER BY m.ts DESC
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        threads: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            peer = row["peer"]
            if peer in seen:
                continue
            seen.add(peer)
            threads.append(
                {
                    "peer": peer,
                    "lastBody": row["body"],
                    "lastAt": row["ts"],
                    "unread": 0,
                }
            )
        return threads

    def list_messages(self, peer: str) -> list[dict[str, Any]]:
        peer_n = normalize_e164(peer) or peer.strip()
        with self._lock:
            self._expire_stale_send_leases(utc_now())
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE peer = ? ORDER BY ts ASC, id ASC",
                (peer_n,),
            ).fetchall()
            self._conn.commit()
        return [_row_message(r) for r in rows]

    def enqueue_outbound(self, to_number: str, body: str) -> dict[str, Any]:
        """Queue one already-segmented message, pacing this destination."""
        return self.enqueue_outbound_many(to_number, [body])[0]

    def enqueue_outbound_many(self, to_number: str, bodies: list[str]) -> list[dict[str, Any]]:
        """Queue ordered parts with a six-second gap for this destination."""
        peer = normalize_e164(to_number) or to_number.strip()
        if not peer:
            raise ValueError("to required")
        if not bodies or any(body is None or not str(body).strip() for body in bodies):
            raise ValueError("body required")
        now = utc_now()
        with self._lock:
            latest = self._conn.execute(
                "SELECT MAX(available_at) AS latest FROM outbox WHERE to_number = ?", (peer,)
            ).fetchone()["latest"]
            available = _parse_utc(now)
            if latest:
                available = max(available, _parse_utc(latest) + timedelta(seconds=OUTBOUND_SMS_GAP_SECONDS))
            queued: list[dict[str, Any]] = []
            for body in bodies:
                msg_id = str(uuid.uuid4())
                available_at = available.isoformat().replace("+00:00", "Z")
                msg = {
                    "id": msg_id, "direction": "out", "peer": peer, "body": str(body),
                    "ts": now, "status": "queued",
                }
                self._conn.execute(
                    "INSERT INTO messages (id, direction, peer, body, ts, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (msg_id, "out", peer, str(body), now, "queued"),
                )
                self._conn.execute(
                    "INSERT INTO outbox (id, to_number, body, created_at, status, available_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (msg_id, peer, str(body), now, "pending", available_at),
                )
                queued.append({
                    "id": msg_id, "to": peer, "body": str(body), "created_at": now,
                    "available_at": available_at, "status": "pending", "message": msg,
                })
                available += timedelta(seconds=OUTBOUND_SMS_GAP_SECONDS)
            self._conn.commit()
        return queued

    def list_outbox_pending(self) -> list[dict[str, Any]]:
        """Rows still in pending (tests / diagnostics only)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox WHERE status = 'pending' ORDER BY available_at ASC, created_at ASC, id ASC"
            ).fetchall()
        return [_row_outbox(r) for r in rows]

    def claim_outbox_for_send(self, limit: int = 20, now: Optional[str] = None) -> list[dict[str, Any]]:
        """Lease at most one due item per destination, in destination order."""
        now = now or utc_now()
        with self._lock:
            self._expire_stale_send_leases(now)
            rows = self._conn.execute(
                """SELECT o.id FROM outbox AS o
                   WHERE o.status = 'pending' AND o.available_at <= ?
                     AND o.id = (
                         SELECT next.id FROM outbox AS next
                         WHERE next.to_number = o.to_number AND next.status = 'pending'
                         ORDER BY next.available_at ASC, next.created_at ASC, next.id ASC LIMIT 1
                     )
                   ORDER BY o.available_at ASC, o.created_at ASC, o.id ASC LIMIT ?""",
                (now, max(1, int(limit))),
            ).fetchall()
            ids = [r["id"] for r in rows]
            if not ids:
                self._conn.commit()
                return []
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE outbox SET status = 'sending', claimed_at = ? WHERE id IN ({placeholders})",
                (now, *ids),
            )
            self._conn.commit()
            claimed = self._conn.execute(
                f"SELECT * FROM outbox WHERE id IN ({placeholders}) ORDER BY available_at ASC, created_at ASC, id ASC",
                ids,
            ).fetchall()
        return [_row_outbox(r) for r in claimed]

    def _expire_stale_send_leases(self, now: str) -> None:
        """Caller holds [_lock]. Timeout only. Do not requeue.

        Requeueing would send a second SMS when the process died after
        SmsManager had already accepted the first one.
        """
        cutoff = (
            _parse_utc(now) - timedelta(seconds=OUTBOX_SEND_LEASE_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        stale = self._conn.execute(
            """SELECT id FROM outbox
               WHERE status = 'sending' AND claimed_at != '' AND claimed_at <= ?""",
            (cutoff,),
        ).fetchall()
        if not stale:
            return
        ids = [row["id"] for row in stale]
        placeholders = ",".join("?" * len(ids))
        reason = "send lease expired"
        self._conn.execute(
            f"UPDATE outbox SET status = 'timeout', error = ? WHERE id IN ({placeholders})",
            (reason, *ids),
        )
        self._conn.execute(
            f"UPDATE messages SET status = 'timeout', error = ? WHERE id IN ({placeholders})",
            (reason, *ids),
        )

    def ack_outbox(self, item_id: str, status: str = "sent", error: Optional[str] = None) -> Optional[dict[str, Any]]:
        status_n = (status or "sent").strip().lower()
        if status_n not in {"sent", "failed", "timeout"}:
            status_n = "sent"
        error_n = (error or "").strip()[:240]
        with self._lock:
            row = self._conn.execute("SELECT * FROM outbox WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            current = row["status"]
            if current in {"sent", "failed", "timeout"}:
                return _row_outbox(row)
            self._conn.execute(
                "UPDATE outbox SET status = ?, error = ? WHERE id = ?",
                (status_n, error_n, item_id),
            )
            self._conn.execute(
                "UPDATE messages SET status = ?, error = ? WHERE id = ?",
                (status_n, error_n, item_id),
            )
            self._conn.commit()
            updated = self._conn.execute("SELECT * FROM outbox WHERE id = ?", (item_id,)).fetchone()
        return _row_outbox(updated)

    def add_call(
        self,
        direction: str,
        number: str,
        started_at: str,
        duration_sec: int,
        switchboard_mode: Optional[str] = None,
        session_id: Optional[str] = None,
        tap_summary: Optional[str] = None,
        call_id: Optional[str] = None,
        path: Optional[str] = None,
    ) -> dict[str, Any]:
        dir_n = (direction or "").strip().lower()
        if dir_n in {"in", "incoming", "inbound"}:
            dir_n = "in"
        elif dir_n in {"out", "outgoing", "outbound"}:
            dir_n = "out"
        number_n = normalize_e164(number) or (number or "").strip()
        item = {
            "id": call_id or str(uuid.uuid4()),
            "direction": dir_n or "in",
            "number": number_n,
            "started_at": started_at or utc_now(),
            "duration_sec": int(duration_sec or 0),
            "switchboard_mode": switchboard_mode or None,
            "session_id": session_id or None,
            "tap_summary": tap_summary or None,
            "path": path if path in ("public", "tailscale") else None,
        }
        with self._lock:
            self._conn.execute(
                """INSERT INTO calls
                   (id, direction, number, started_at, duration_sec, switchboard_mode, session_id, tap_summary, path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["id"],
                    item["direction"],
                    item["number"],
                    item["started_at"],
                    item["duration_sec"],
                    item["switchboard_mode"],
                    item["session_id"],
                    item["tap_summary"],
                    item["path"],
                ),
            )
            self._conn.commit()
        return item

    def list_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM calls ORDER BY started_at DESC").fetchall()
        return [_row_call(r) for r in rows]

    def get_call(self, call_id: str) -> Optional[dict[str, Any]]:
        if not call_id:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        return _row_call(row) if row else None

    def add_link_event(
        self,
        kind: str,
        session_id: Optional[str] = None,
        close_code: Optional[int] = None,
        close_reason: Optional[str] = None,
        initiator: Optional[str] = None,
        detail: Optional[str] = None,
        ts: Optional[str] = None,
        entrance: Optional[str] = None,
    ) -> dict[str, Any]:
        item = {
            "ts": ts or utc_now(),
            "session_id": session_id or None,
            "kind": (kind or "close").strip() or "close",
            "close_code": int(close_code) if close_code is not None else None,
            "close_reason": close_reason or None,
            "initiator": initiator or None,
            "detail": detail or None,
            "entrance": entrance if entrance in ("public", "tailscale") else None,
        }
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO call_link_events
                   (ts, session_id, kind, close_code, close_reason, initiator, detail, entrance)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["ts"],
                    item["session_id"],
                    item["kind"],
                    item["close_code"],
                    item["close_reason"],
                    item["initiator"],
                    item["detail"],
                    item["entrance"],
                ),
            )
            self._conn.commit()
            item["id"] = cur.lastrowid
        return item

    def list_link_events(self, limit: int = 50) -> list[dict[str, Any]]:
        cap = max(1, min(int(limit or 50), 500))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM call_link_events ORDER BY id DESC LIMIT ?",
                (cap,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_message(self, msg_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return _row_message(row) if row else None


def reset_for_tests(path: Optional[Path] = None) -> PortalStore:
    """Repoint the imported hub process at a fresh store.

    hub.py builds portal_store at import. Tests set GSM2COMPUTER_PORTAL_DB
    afterwards and call this so HTTP handlers see the temp database.
    """
    import hub as hub_mod

    target = Path(path) if path is not None else Path(
        os.environ.get("GSM2COMPUTER_PORTAL_DB", str(HUB_DIR / "portal.sqlite"))
    )
    try:
        hub_mod.portal_store.close()
    except Exception:
        pass
    store = PortalStore(target)
    hub_mod.portal_store = store
    hub_mod.portal_app.store = store
    return store
