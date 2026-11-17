#!/usr/bin/env python3
"""Optional outbound POST of system alerts to a Grok Bot webhook routine.

Unset GSM2COMPUTER_ALERT_WEBHOOK_URL is a silent no-op. Failures never raise
into the call path. Do not log the webhook key or Authorization header.

Synthetic OpenClaw e2e / self-test alerts are skipped (see is_test_alert).
Structured call_ended events (including dry-run test:true) always post —
they must wake Cup after real phone hangups and must not be swallowed by
the e2e text filter.

Inbound SMS use a second destination when GSM2COMPUTER_SMS_WEBHOOK_URL is
set (see post_sms_event). That queue is a separate spool file and worker.
The hub sends every non-duplicate inbound SMS there, and also posts the
ops text when the body looks like a system-down, including STATUS. An unset
SMS URL leaves that queue unused; only should_wake_cup texts go to ops.
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
from typing import Any, Iterator, Mapping
import urllib.error
import urllib.request
import uuid

LOG = logging.getLogger("gsm2computer-hub")

URL_ENV = "GSM2COMPUTER_ALERT_WEBHOOK_URL"
KEY_ENV = "GSM2COMPUTER_ALERT_WEBHOOK_KEY"
SMS_URL_ENV = "GSM2COMPUTER_SMS_WEBHOOK_URL"
SMS_KEY_ENV = "GSM2COMPUTER_SMS_WEBHOOK_KEY"
TIMEOUT_S = 5.0

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


class _Destination:
    """One webhook URL and its private sqlite queue."""

    def __init__(
        self,
        name: str,
        url_env: str,
        key_env: str,
        spool_env: str,
        default_spool: str,
        thread_name: str,
    ) -> None:
        self.name = name
        self.url_env = url_env
        self.key_env = key_env
        self.spool_env = spool_env
        self.default_spool = default_spool
        self.thread_name = thread_name
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.pid: int | None = None

    def url(self) -> str:
        return (os.environ.get(self.url_env) or "").strip()

    def key(self) -> str:
        return (os.environ.get(self.key_env) or "").strip()

    def spool_path(self) -> Path:
        raw = (os.environ.get(self.spool_env) or "").strip()
        if raw:
            return Path(raw)
        return Path.home() / self.default_spool


# Private durable outboxes. The ops queue path stays the historical default so
# an already-spooled call_ended row is still delivered. SMS uses another file
# and another thread: a stuck POST on one destination cannot hold the other.
SPOOL_ENV = "GSM2COMPUTER_ALERT_SPOOL"
SMS_SPOOL_ENV = "GSM2COMPUTER_SMS_SPOOL"
MAX_TEXT_BYTES = 4096

ALERT = _Destination(
    "alert",
    URL_ENV,
    KEY_ENV,
    SPOOL_ENV,
    ".local/state/gsm2computer/alert-webhook/outbox.sqlite3",
    "alert-webhook",
)
SMS = _Destination(
    "sms",
    SMS_URL_ENV,
    SMS_KEY_ENV,
    SMS_SPOOL_ENV,
    ".local/state/gsm2computer/sms-webhook/outbox.sqlite3",
    "sms-webhook",
)
_worker_lock = ALERT.lock
_wake = ALERT.wake


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
    return ALERT.spool_path()


@contextmanager
def _connect_path(path: Path) -> Iterator[sqlite3.Connection]:
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
        status INTEGER, response TEXT, delivered REAL, lease TEXT)""")
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    with _connect_path(_spool_path()) as conn:
        yield conn


def _deliver(dest: _Destination, payload: str) -> tuple[int, str]:
    url = dest.url()
    key = dest.key()
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


def _send(payload: str) -> tuple[int, str]:
    """POST one ops-alert body. Tests patch this; SMS delivery does not."""
    return _deliver(ALERT, payload)


def _drain_dest(dest: _Destination) -> bool:
    if not dest.url():
        return False
    now = time.time()
    lease = uuid.uuid4().hex
    with _connect_path(dest.spool_path()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM alerts WHERE delivered IS NULL AND due <= ? "
                           "ORDER BY due, created LIMIT 1", (now,)).fetchone()
        if row is None:
            return False
        # Lease expires after a crash; HTTP timeout is only 5 seconds.
        conn.execute("UPDATE alerts SET due=?, lease=? WHERE id=?", (now + 60, lease, row["id"]))
    # Ops alerts go through _send so an existing patch of that function still
    # covers the ops queue. SMS never calls it, so the two POSTs do not share
    # a worker or a lock.
    if dest is ALERT:
        status, detail = _send(row["payload"])
    else:
        status, detail = _deliver(dest, row["payload"])
    attempts = row["attempts"] + 1
    ok = 200 <= status < 300
    delay = min(900, 30 * 2 ** min(attempts - 1, 5)) + random.uniform(0, 5)
    with _connect_path(dest.spool_path()) as conn:
        conn.execute("UPDATE alerts SET attempts=?, status=?, response=?, delivered=?, due=?, lease=NULL "
                     "WHERE id=? AND lease=?",
                     (attempts, status, detail, time.time() if ok else None,
                      time.time() + delay, row["id"], lease))
        # Keep exact successful/failed request and response pairs privately for 7 days.
        conn.execute("DELETE FROM alerts WHERE delivered < ?", (time.time() - 7 * 86400,))
    kind = "alert webhook" if dest is ALERT else "sms webhook"
    if ok:
        LOG.info("%s delivered id=%s attempts=%s HTTP %s bytes=%s",
                 kind, row["id"], attempts, status, len(row["payload"].encode("utf-8")))
    else:
        LOG.warning("%s HTTP %s id=%s attempt=%s retry_s=%.0f bytes=%s response=%r",
                    kind, status, row["id"], attempts, delay,
                    len(row["payload"].encode("utf-8")), detail)
    return True


def _drain_one() -> bool:
    return _drain_dest(ALERT)


def _run_worker(dest: _Destination) -> None:
    while True:
        try:
            if _drain_dest(dest):
                continue
        except Exception as exc:
            kind = "alert" if dest is ALERT else "sms"
            LOG.warning("%s webhook spool worker failed (%s)", kind, type(exc).__name__)
        dest.wake.wait(5)
        dest.wake.clear()


def _ensure_worker(dest: _Destination) -> None:
    if not dest.url():
        return
    with dest.lock:
        if dest.pid != os.getpid():
            threading.Thread(
                target=_run_worker, args=(dest,), name=dest.thread_name, daemon=True
            ).start()
            dest.pid = os.getpid()


def sms_webhook_configured() -> bool:
    """True when GSM2COMPUTER_SMS_WEBHOOK_URL is set to a non-blank URL."""
    return bool(SMS.url())


def start_sms_worker() -> None:
    """Resume persisted SMS events. A no-op when the SMS URL is unset."""
    _ensure_worker(SMS)


def start_alert_worker() -> None:
    """Resume persisted alerts on startup, without blocking the hub event loop.

    Also resumes the SMS queue when that URL is set. The workers are separate
    threads, so one destination's HTTP call cannot stall the other.
    """
    _ensure_worker(ALERT)
    _ensure_worker(SMS)


def _enqueue(payload: Mapping[str, Any], dest: _Destination) -> None:
    if not dest.url():
        return
    try:
        original = json.dumps(dict(payload), ensure_ascii=True, allow_nan=False)
        body = json.dumps(_prepare_payload(payload), ensure_ascii=False, allow_nan=False)
        alert_id = uuid.uuid4().hex
        with _connect_path(dest.spool_path()) as conn:
            conn.execute("INSERT INTO alerts(id, original_payload, payload, created, due) VALUES(?,?,?,?,?)",
                         (alert_id, original, body, time.time(), time.time()))
        if dest is ALERT:
            start_alert_worker()
        else:
            start_sms_worker()
        dest.wake.set()
    except Exception as exc:
        kind = "alert" if dest is ALERT else "sms"
        LOG.error("%s webhook enqueue failed (%s)", kind, type(exc).__name__)


def _post_json(payload: Mapping[str, Any]) -> None:
    _enqueue(payload, ALERT)


def post_system_alert(text: str, *, e2e: bool = False) -> None:
    """Durably enqueue ``{"text": ...}``, or skip if unset/test."""
    if is_test_alert(text, e2e=e2e):
        LOG.info("alert webhook skipped (e2e/test): %s", (text or "")[:160])
        return
    _post_json({"text": text})


def post_sms_event(
    text: str,
    *,
    sender: str,
    body: str,
    received_at: str,
) -> None:
    """Enqueue one inbound SMS on the SMS webhook, or the ops webhook if unset.

    With GSM2COMPUTER_SMS_WEBHOOK_URL set, the payload is today's ops text
    plus event/from/body/receivedAt, and this function does not touch the
    ops queue. The hub posts post_system_alert separately when the body
    looks like a system-down, including STATUS.

    With the URL unset, this is post_system_alert(text). The hub uses that
    only for should_wake_cup messages.
    """
    if SMS.url():
        if is_test_alert(text):
            LOG.info("sms webhook skipped (e2e/test): %s", (text or "")[:160])
            return
        _enqueue(
            {
                "text": text,
                "event": "sms_in",
                "from": sender,
                "body": body,
                "receivedAt": received_at,
            },
            SMS,
        )
        return
    post_system_alert(text)


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
