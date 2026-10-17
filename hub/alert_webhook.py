#!/usr/bin/env python3
"""Optional outbound POST of system alerts to a Grok Bot webhook routine.

Unset GSM2COMPUTER_ALERT_WEBHOOK_URL is a silent no-op. Failures never raise
into the call path. Do not log the webhook key or Authorization header.

Synthetic OpenClaw e2e / self-test alerts are skipped (see is_test_alert).
Structured call_ended events (including dry-run test:true) always post —
they must wake Cup after real phone hangups and must not be swallowed by
the e2e text filter.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import random
import re
import sqlite3
import threading
import time
from typing import Any, Mapping
import urllib.error
import urllib.request
import uuid

LOG = logging.getLogger("gsm2computer-hub")

URL_ENV = "GSM2COMPUTER_ALERT_WEBHOOK_URL"
KEY_ENV = "GSM2COMPUTER_ALERT_WEBHOOK_KEY"
SMS_URL_ENV = "GSM2COMPUTER_SMS_WEBHOOK_URL"
SMS_KEY_ENV = "GSM2COMPUTER_SMS_WEBHOOK_KEY"
TIMEOUT_S = 5.0

DESTINATION_ALERT = "alert"
DESTINATION_SMS = "sms"

# Match e2e / self-test wording so test cleanup never wakes Cup.
_TEST_ALERT_RE = re.compile(
    r"(e2e-test|/e2e-test|\be2e\b|openclaw e2e|self-test|synthetic test)",
    re.IGNORECASE,
)


def is_test_alert(text: str, *, e2e: bool = False) -> bool:
    """True when this alert is about a synthetic e2e session (must not wake Cup)."""
    if e2e:
        return True
    return bool(_TEST_ALERT_RE.search(text or ""))


def _authorization_value(key: str) -> str:
    if key.lower().startswith("bearer "):
        return key
    return f"Bearer {key}"


# Private durable outbox, shared safely by hub and call-end watcher processes.
SPOOL_ENV = "GSM2COMPUTER_ALERT_SPOOL"
MAX_TEXT_BYTES = 4096
_worker_lock = threading.Lock()
_worker_pid = None
_wake = threading.Event()


def _safe_detail(value: str) -> str:
    # Redact before truncating: a boundary must not reveal part of a credential.
    for secret in (
        os.environ.get(URL_ENV, ""),
        os.environ.get(KEY_ENV, ""),
        os.environ.get(SMS_URL_ENV, ""),
        os.environ.get(SMS_KEY_ENV, ""),
    ):
        secret = secret.strip()
        if secret:
            value = value.replace(secret, "[REDACTED]")
            if secret.lower().startswith("bearer "):
                value = value.replace(secret[7:], "[REDACTED]")
    value = re.sub(r"https?://[^\s\"<>]+", "[URL REDACTED]", value)
    value = re.sub(r"(?i)(authorization|bearer)\s*[:=]?\s*[^\r\n]+", "[REDACTED]", value)
    return value.encode("utf-8", "replace").decode("utf-8")[:2048]


def _prepare_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if isinstance(result.get("text"), str):
        text = result["text"].encode("utf-8", "replace").decode("utf-8")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            text = encoded[:MAX_TEXT_BYTES - 14].decode("utf-8", "ignore") + "...[truncated]"
        result["text"] = text
    return result


def _spool_path() -> Path:
    return Path(os.environ.get(SPOOL_ENV) or
                str(Path.home() / ".local/state/gsm2computer/alert-webhook/outbox.sqlite3"))


def _destination_config(destination: str) -> tuple[str, str]:
    if destination == DESTINATION_SMS:
        return (
            (os.environ.get(SMS_URL_ENV) or "").strip(),
            (os.environ.get(SMS_KEY_ENV) or "").strip(),
        )
    return (
        (os.environ.get(URL_ENV) or "").strip(),
        (os.environ.get(KEY_ENV) or "").strip(),
    )


def _destination_is_configured(destination: str) -> bool:
    return bool(_destination_config(destination)[0])


def _configured_destinations() -> tuple[str, ...]:
    return tuple(
        destination
        for destination in (DESTINATION_ALERT, DESTINATION_SMS)
        if _destination_is_configured(destination)
    )


@contextmanager
def _connect():
    path = _spool_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Create privately before SQLite opens it (also protects WAL/SHM in private dir).
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    path.chmod(0o600)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY, original_payload TEXT NOT NULL, payload TEXT NOT NULL,
        created REAL NOT NULL, due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        status INTEGER, response TEXT, delivered REAL, lease TEXT,
        destination TEXT NOT NULL DEFAULT 'alert')""")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
    if "destination" not in columns:
        # Pre-split spool rows were all alert-webhook deliveries.
        conn.execute("ALTER TABLE alerts ADD COLUMN destination TEXT NOT NULL DEFAULT 'alert'")
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _send(payload: str, destination: str) -> tuple[int, str]:
    url, key = _destination_config(destination)
    headers = {"Content-Type": "application/json", "User-Agent": "gsm2computer-alert/2"}
    if key:
        headers["Authorization"] = _authorization_value(key)
    req = urllib.request.Request(url, data=payload.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, _safe_detail(resp.read(8192).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, _safe_detail(exc.read(8192).decode("utf-8", "replace"))
    except Exception as exc:
        # Exception messages can contain the destination or headers.
        return 0, type(exc).__name__


def _drain_one() -> bool:
    destinations = _configured_destinations()
    if not destinations:
        return False
    now = time.time()
    lease = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in destinations)
        row = conn.execute(
            "SELECT * FROM alerts WHERE delivered IS NULL AND due <= ? "
            f"AND destination IN ({placeholders}) ORDER BY due, created LIMIT 1",
            (now, *destinations),
        ).fetchone()
        if row is None:
            return False
        # Lease expires after a crash; HTTP timeout is only 5 seconds.
        conn.execute("UPDATE alerts SET due=?, lease=? WHERE id=?", (now + 60, lease, row["id"]))
    status, detail = _send(row["payload"], row["destination"])
    attempts = row["attempts"] + 1
    ok = 200 <= status < 300
    delay = min(900, 30 * 2 ** min(attempts - 1, 5)) + random.uniform(0, 5)
    with _connect() as conn:
        conn.execute("UPDATE alerts SET attempts=?, status=?, response=?, delivered=?, due=?, lease=NULL "
                     "WHERE id=? AND lease=?",
                     (attempts, status, detail, time.time() if ok else None,
                      time.time() + delay, row["id"], lease))
        # Keep exact successful/failed request and response pairs privately for 7 days.
        conn.execute("DELETE FROM alerts WHERE delivered < ?", (time.time() - 7 * 86400,))
    if ok:
        LOG.info("alert webhook delivered id=%s attempts=%s HTTP %s bytes=%s",
                 row["id"], attempts, status, len(row["payload"].encode("utf-8")))
    else:
        LOG.warning("alert webhook HTTP %s id=%s attempt=%s retry_s=%.0f bytes=%s response=%r",
                    status, row["id"], attempts, delay,
                    len(row["payload"].encode("utf-8")), detail)
    return True


def _run_worker() -> None:
    while True:
        try:
            if _drain_one():
                continue
        except Exception as exc:
            LOG.warning("alert webhook spool worker failed (%s)", type(exc).__name__)
        _wake.wait(5)
        _wake.clear()


def start_alert_worker() -> None:
    """Resume persisted alerts on startup, without blocking the hub event loop."""
    global _worker_pid
    if not _configured_destinations():
        return
    with _worker_lock:
        if _worker_pid != os.getpid():
            threading.Thread(target=_run_worker, name="alert-webhook", daemon=True).start()
            _worker_pid = os.getpid()


def _post_json(payload: Mapping[str, Any], *, destination: str = DESTINATION_ALERT) -> None:
    if not _destination_is_configured(destination):
        return
    try:
        original = json.dumps(dict(payload), ensure_ascii=True, allow_nan=False)
        body = json.dumps(_prepare_payload(payload), ensure_ascii=False, allow_nan=False)
        alert_id = uuid.uuid4().hex
        with _connect() as conn:
            conn.execute(
                "INSERT INTO alerts(id, original_payload, payload, created, due, destination) "
                "VALUES(?,?,?,?,?,?)",
                (alert_id, original, body, time.time(), time.time(), destination),
            )
        start_alert_worker()
        _wake.set()
    except Exception as exc:
        LOG.error("alert webhook enqueue failed (%s)", type(exc).__name__)


def post_system_alert(text: str, *, e2e: bool = False) -> None:
    """Durably enqueue ``{"text": ...}``, or skip if unset/test."""
    if is_test_alert(text, e2e=e2e):
        LOG.info("alert webhook skipped (e2e/test): %s", (text or "")[:160])
        return
    _post_json({"text": text})


def post_inbound_sms_alert(text: str) -> None:
    """Durably enqueue an inbound-SMS alert at its optional destination.

    With no SMS webhook URL configured, preserve the legacy alert destination.
    """
    destination = DESTINATION_SMS if _destination_is_configured(DESTINATION_SMS) else DESTINATION_ALERT
    _post_json({"text": text}, destination=destination)


def post_alert_event(payload: Mapping[str, Any]) -> None:
    """POST a structured JSON event (e.g. call_ended).

    Always allowed for ``event == "call_ended"`` (including ``test: true`` dry-runs).
    Other events with ``e2e: true`` are skipped. Never logs secrets.
    """
    event = str(payload.get("event") or "")
    if event == "call_ended":
        LOG.info(
            "alert webhook call_ended test=%s duration_s=%s call_id=%s",
            bool(payload.get("test")),
            payload.get("duration_s"),
            str(payload.get("call_id") or "")[:32],
        )
        _post_json(payload)
        return
    if payload.get("e2e") is True:
        LOG.info("alert webhook skipped (e2e event)")
        return
    text = str(payload.get("text") or "")
    if is_test_alert(text, e2e=False):
        LOG.info("alert webhook skipped (e2e/test event text)")
        return
    _post_json(payload)
